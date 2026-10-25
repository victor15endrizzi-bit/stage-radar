"""Tests du radar : parsing, scoring, dédoublonnage, expiration, alertes, persistance.

Les connecteurs sont testés sur des pages d'exemple (tests/fixtures) et des réponses HTTP simulées :
aucun accès réseau n'est nécessaire. Le fonctionnement réel sur les sites est vérifié par
`python -m radar check-source <id>` (GitHub : Actions → Radar → mode check-source).
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from radar.connectors.email_alerts import parse_alert_html
from radar.connectors.links import parse_links
from radar.connectors.moovijob import parse_detail, parse_listing
from radar.connectors.workday import WorkdayConnector
from radar.models import RawJob, SourceResult
from radar.notify import format_job_message
from radar.pipeline import Runner, in_quiet_hours, send_daily_digest, send_pending_alerts
from radar.scoring import analyze
from radar.store import MemoryStore
from radar.textutil import company_norm, extract_duration, extract_start, title_key

FIX = Path(__file__).parent / "fixtures"
ROOT = Path(__file__).parent.parent
SETTINGS = yaml.safe_load((ROOT / "config/profile.yaml").read_text())
NOW = datetime(2026, 10, 24, 10, 0, tzinfo=timezone.utc)  # 12h à Paris


# ----------------------------------------------------------------------------- texte
def test_company_norm_aliases():
    assert company_norm("Banque Internationale à Luxembourg (BIL)") == "bil"
    assert company_norm("Spuerkeess") == company_norm("BCEE") == "spuerkeess"
    assert company_norm("Massena Partners S.A.") == "massena partners"
    assert company_norm("Banque de Luxembourg") == "banque de luxembourg"
    assert company_norm("Foyer S.A.") == "foyer"


def test_title_key_ignores_gender_and_internship_words():
    assert title_key("Compliance Intern (F/M/X)") == title_key("Stage - Compliance (H/F)") == "compliance"
    assert title_key("Fund Analyst - 6 months internship") == "fund analyst"


def test_duration_and_start():
    assert extract_duration("Stage de 6 mois à partir de janvier 2027") == "6 mois"
    assert extract_duration("Internship 4 to 6 months") == "4 à 6 mois"
    assert extract_start("à partir de janvier 2027 !") == "janvier 2027"
    assert extract_start("starting 01/03/2027") == "mars 2027"
    assert extract_start("Start date: as soon as possible") == "dès que possible"


# ----------------------------------------------------------------------------- connecteurs
def test_moovijob_listing():
    jobs, has_next = parse_listing((FIX / "moovijob_list.html").read_text(), "moovijob-stages", True, now=NOW)
    titles = [j.title for j in jobs]
    assert has_next
    assert len(jobs) == 4, titles  # doublon et annonce « salon » exclus, lien de filtre ignoré
    m = jobs[0]
    assert m.company == "Massena Partners S.A." and m.location == "Luxembourg"
    assert m.contract == "Stage" and m.is_internship_hint is True
    assert m.published_at == NOW - timedelta(hours=2)
    foyer = next(j for j in jobs if "Compliance" in j.title)
    assert foyer.url == "https://www.moovijob.com/offres-emploi/foyer/compliance-intern-fmx"


def test_moovijob_detail():
    j = RawJob("moovijob-stages", "x", "https://x", "Stage conventionné - Debt Capital Markets (m/f/n)", "Spuerkeess")
    parse_detail((FIX / "moovijob_detail.html").read_text(), j)
    assert j.published_at.date().isoformat() == "2026-10-10" and not j.published_is_estimate
    assert j.languages == ["EN", "FR"]
    assert "Debt and Structured Products" in j.description


class FakeResp:
    def __init__(self, data=None, text=""):
        self._d, self.text, self.status_code = data, text, 200

    def json(self):
        return self._d

    def raise_for_status(self):
        pass


class FakeSession:
    """Simule les réponses HTTP (Workday) sans réseau."""

    def __init__(self, routes):
        self.routes, self.headers, self.calls = routes, {}, []

    def _hit(self, url, **kw):
        self.calls.append((url, kw.get("json")))
        for pat, resp in self.routes.items():
            if re.search(pat, url):
                return resp(url, kw) if callable(resp) else resp
        raise AssertionError("URL non prévue : " + url)

    def get(self, url, **kw):
        return self._hit(url, **kw)

    def post(self, url, **kw):
        return self._hit(url, **kw)


def test_workday_connector():
    postings = {"total": 3, "jobPostings": [
        {"title": "Product Management Intern", "externalPath": "/job/Luxembourg/Product-Management-Intern_R1",
         "locationsText": "Luxembourg 2 Blvd K. Adenauer", "postedOn": "Posted 2 Days Ago"},
        {"title": "Intern Frankfurt", "externalPath": "/job/Frankfurt/Intern_R2", "locationsText": "Frankfurt"},
        {"title": "Risk Intern", "externalPath": "/job/x/Risk-Intern_R3", "locationsText": "2 Locations"},
    ]}
    detail = {"jobPostingInfo": {"title": "Product Management Intern", "startDate": "2026-10-20",
                                 "jobDescription": "<p>Your tasks</p><ul><li>Support fund product launches (UCITS)</li></ul>",
                                 "location": "Luxembourg", "externalUrl": "https://db.wd3.myworkdayjobs.com/DBWebsite/job/R1"}}
    detail3 = {"jobPostingInfo": {"title": "Risk Intern", "jobDescription": "x", "location": "London", "additionalLocations": ["Paris"]}}
    sess = FakeSession({r"/jobs$": FakeResp(postings), r"_R1$": FakeResp(detail), r"_R3$": FakeResp(detail3)})
    src = {"id": "wd", "name": "Deutsche Bank / DWS (Workday)", "kind": "workday",
           "config": {"host": "db.wd3.myworkdayjobs.com", "tenant": "db", "site": "DBWebsite",
                      "searches": ["Luxembourg intern"], "location_must_contain": ["luxemb"]}}
    c = WorkdayConnector(src, session=sess)
    c.delay_seconds = 0
    res = c.fetch()
    assert [j.title for j in res.jobs] == ["Product Management Intern", "Risk Intern"]  # Francfort exclu
    assert sess.calls[0][1]["searchText"] == "Luxembourg intern"
    j = c.enrich(res.jobs[0])
    assert j.url.endswith("/job/R1") and j.published_at.date().isoformat() == "2026-10-20"
    assert "UCITS" in j.description
    assert c.enrich(res.jobs[1]).extra.get("drop") == "hors zone"


def test_links_quintet_style():
    html = """<ul><li><a href="https://www.quintet.com/en-gb/vacancies/job/market-risk-specialist/3382">Market Risk Specialist (Luxembourg, LUX, L-2449)</a></li>
    <li><a href="https://www.quintet.com/en-gb/vacancies/job/client-advisor/3413">Client Advisor (Manchester, GBR, M3 3HF)</a></li>
    <li><a href="/en-gb/vacancies">Vacancies</a></li></ul>"""
    jobs = parse_links(html, "https://www.quintet.com", re.compile(r"/vacancies/job/[^\"'#?]+/\d+"), "quintet",
                       "Quintet Private Bank", ["luxembourg"])
    assert len(jobs) == 1 and jobs[0].title == "Market Risk Specialist" and "LUX" in jobs[0].location


def test_email_alert_linkedin():
    jobs = parse_alert_html((FIX / "linkedin_alert.html").read_text(), "email-alerts", NOW)
    assert {j.external_id for j in jobs} == {"https://www.linkedin.com/jobs/view/4012345678",
                                            "https://www.linkedin.com/jobs/view/4099999999"}
    fa = next(j for j in jobs if "Fund Analyst" in j.title)
    assert fa.company == "Candriam" and fa.location.startswith("Luxembourg")


# ----------------------------------------------------------------------------- scoring
def score(title, desc="", company="X", hint=True, published=None):
    return analyze(RawJob("s", "e", "u", title, company, description=desc, is_internship_hint=hint,
                          published_at=published), SETTINGS, now=NOW)


def test_scoring_ranks_targets_first():
    pm = score("Internship Luxembourg – Multi-Asset Strategist F/M/D", "Master in finance. CFA. Excel, Python.", "Candriam")
    dcm = score("Stage conventionné - Debt Capital Markets", "Master. structured products", "Spuerkeess")
    rm = score("Trainee - Associate Relationship Manager", "wealth management", "BIL")
    hr = score("Stagiaire - RH", "ressources humaines, recrutement", "POST Luxembourg")
    reception = score("Stagiaire Réception H/F", "accueil des clients", "IBIS")
    assert pm.priority == "prioritaire" and pm.score >= 75, pm.details
    assert dcm.priority == "prioritaire", dcm.details
    assert rm.priority == "interessante", rm.details
    assert hr.priority == "faible" and reception.priority == "faible"
    assert pm.score > dcm.score - 15 and dcm.score > rm.score > hr.score
    assert all(isinstance(d["points"], int) for d in pm.details)
    assert pm.reasons and "Métier" in pm.reasons[0]


def test_scoring_unusual_title_found_through_missions():
    j = score("Junior Analyst Programme", "You will support the portfolio managers in asset allocation, "
              "fund selection and performance attribution of multi-asset funds.", "Some Bank")
    assert j.score >= 35, j.details  # intitulé vague, mais missions AM -> pas noyé
    assert j.domain == "am_front"


def test_scoring_non_internship_and_feedback():
    cdi = score("Portfolio Manager", "5 years experience", hint=False)
    assert cdi.priority == "hors_stage"
    base = score("Fund Analyst Intern", "", "Acme AM")
    boosted = analyze(RawJob("s", "e", "u", "Fund Analyst Intern", "Acme AM", is_internship_hint=True),
                      SETTINGS, company_bias={"acme am": 12}, now=NOW)
    assert boosted.score == min(100, base.score + 12)


# ----------------------------------------------------------------------------- pipeline
class FakeConnector:
    """Connecteur scripté pour tester le pipeline de bout en bout."""
    batches: dict = {}

    def __init__(self, source, session=None):
        self.source = source

    def fetch(self):
        b = FakeConnector.batches[self.source["id"]]
        if isinstance(b, Exception):
            raise b
        return SourceResult(jobs=[RawJob(**{**j, "source_id": self.source["id"]}) for j in b], complete=True)

    def enrich(self, job):
        return job


class FakeTG:
    enabled = True

    def __init__(self):
        self.msgs = []

    def send(self, text, buttons=None, silent=False):
        self.msgs.append((text, buttons, silent))
        return True


@pytest.fixture
def env(monkeypatch):
    import radar.pipeline as pl
    monkeypatch.setattr(pl, "make_connector", lambda s, session=None: FakeConnector(s))
    st = MemoryStore()
    st.insert("sources", [
        {"id": "moov", "name": "Moovijob", "kind": "x", "enabled": True, "interval_minutes": 20, "last_success_at": "2026-01-01"},
        {"id": "wd", "name": "Workday", "kind": "x", "enabled": True, "interval_minutes": 30, "target": True, "last_success_at": "2026-01-01"},
    ])
    return st, FakeTG()


def J(eid, title, company, desc="Master finance", **kw):
    return {"external_id": eid, "url": f"https://ex/{eid}", "title": title, "company": company, "description": desc,
            "is_internship_hint": True, **kw}


def test_pipeline_new_dedup_notify_once(env):
    st, tg = env
    FakeConnector.batches = {
        "moov": [J("m1", "Stage - Fund Analyst (H/F)", "Candriam S.A.", "fund selection, multi-asset"),
                 J("m2", "Stagiaire Réception", "IBIS", "accueil")],
        "wd": [J("w1", "Fund Analyst Internship", "Candriam", "fund selection, multi-asset, CFA")],
    }
    r = Runner(st, SETTINGS, tg, now=NOW).run(force=True)
    assert r["new_jobs"] == 2 and r["merged"] == 1  # même offre sur 2 sites -> 1 seule fiche
    jobs = st.select("jobs")
    fa = next(j for j in jobs if "Fund Analyst" in j["title"])
    assert len(st.select("job_links", eq={"job_id": fa["id"]})) == 2
    assert send_pending_alerts(st, SETTINGS, tg, now=NOW) == 1  # la réception (faible) n'est pas alertée
    msg, buttons, silent = tg.msgs[0]
    assert "Fund Analyst" in msg and buttons[0][0]["url"] == fa["url"] and "Moovijob" in msg and "Workday" in msg
    # 2e passage : rien de nouveau, aucune alerte répétée
    r2 = Runner(st, SETTINGS, tg, now=NOW + timedelta(hours=1)).run(force=True)
    assert r2["new_jobs"] == 0 and send_pending_alerts(st, SETTINGS, tg, now=NOW + timedelta(hours=1)) == 0
    # le récap du matin reprend l'offre peu pertinente, une seule fois
    txt = send_daily_digest(st, SETTINGS, tg, now=NOW)
    assert "Réception" in txt
    assert "Réception" not in send_daily_digest(st, SETTINGS, tg, now=NOW + timedelta(days=1))


def test_pipeline_expiry_requires_all_sources_gone(env):
    st, tg = env
    FakeConnector.batches = {"moov": [J("m1", "Risk Intern", "Pictet")], "wd": [J("w1", "Risk Internship", "Pictet")]}
    Runner(st, SETTINGS, tg, now=NOW).run(force=True)
    FakeConnector.batches = {"moov": [], "wd": [J("w1", "Risk Internship", "Pictet")]}
    for h in (1, 2, 3):
        Runner(st, SETTINGS, tg, now=NOW + timedelta(hours=h)).run(force=True)
    assert st.select("jobs")[0]["active"] is True  # encore visible sur Workday
    FakeConnector.batches = {"moov": [], "wd": []}
    for h in (4, 5):
        Runner(st, SETTINGS, tg, now=NOW + timedelta(hours=h)).run(force=True)
    job = st.select("jobs")[0]
    assert job["active"] is False and job["expired_at"]


def test_source_failure_alert_once_then_recovery(env):
    st, tg = env
    FakeConnector.batches = {"moov": RuntimeError("page changée"), "wd": []}
    for h in range(4):
        Runner(st, SETTINGS, tg, now=NOW + timedelta(hours=h)).run(force=True)
    alerts = [m for m in tg.msgs if "Source en panne" in m[0]]
    assert len(alerts) == 1 and "page changée" in alerts[0][0]
    FakeConnector.batches["moov"] = []
    Runner(st, SETTINGS, tg, now=NOW + timedelta(hours=5)).run(force=True)
    assert any("Source réparée" in m[0] for m in tg.msgs)
    assert len(st.select("source_runs")) == 10


def test_first_run_is_a_summary_not_a_flood(env):
    st, tg = env
    st.update("sources", {"last_success_at": None}, eq={"id": "moov"})
    FakeConnector.batches = {"moov": [J(f"m{i}", f"Portfolio Management Intern {i} team", f"AM{i}") for i in range(30)], "wd": []}
    Runner(st, SETTINGS, tg, now=NOW).run(force=True)
    assert send_pending_alerts(st, SETTINGS, tg, now=NOW) == 0  # déjà en ligne avant le radar
    assert all(j["initial_import"] for j in st.select("jobs"))


def test_quiet_hours():
    assert in_quiet_hours(datetime(2026, 10, 24, 22, 30, tzinfo=timezone.utc), SETTINGS)  # 00h30 Paris
    assert not in_quiet_hours(NOW, SETTINGS)


def test_message_format_contains_required_fields():
    job = {"id": 3, "title": "Fund Analyst <Intern>", "company": "Candriam", "location": "Luxembourg", "score": 81,
           "priority": "prioritaire", "url": "https://x", "published_at": "2026-10-23T08:00:00+00:00",
           "published_is_estimate": False, "detected_at": "2026-10-24T10:00:00+00:00", "duration": "6 mois",
           "start_date": "mai 2027", "languages": ["EN"], "source_names": ["Moovijob"],
           "analysis": {"reasons": ["Métier — AM"], "missions": ["Analyse de fonds"], "requirements": ["Master"]}}
    m = format_job_message(job)
    for frag in ["81/100", "PRIORITAIRE", "Fund Analyst &lt;Intern&gt;", "Candriam", "23/10/2026", "24/10 12:00",
                 "6 mois", "mai 2027", "Pourquoi", "Missions", "Prérequis", "Moovijob"]:
        assert frag in m, frag


def test_oracle_and_smartrecruiters_connectors():
    from radar.connectors.oracle_hcm import OracleHCMConnector
    from radar.connectors.smartrecruiters import SmartRecruitersConnector
    lst = {"items": [{"TotalJobsCount": 2, "requisitionList": [
        {"Id": "210", "Title": "Fund Services Intern", "PrimaryLocation": "Senningerberg, Luxembourg", "PostedDate": "2026-10-20"},
        {"Id": "211", "Title": "Analyst", "PrimaryLocation": "London, United Kingdom", "PostedDate": "2026-10-20"}]}]}
    det = {"items": [{"ExternalDescriptionStr": "<p>Support the transfer agency team</p>", "JobSchedule": "Full time"}]}
    sess = FakeSession({r"recruitingCEJobRequisitions$": FakeResp(lst), r"Details$": FakeResp(det)})
    c = OracleHCMConnector({"id": "or", "name": "JPM", "config": {"host": "h.oraclecloud.com", "site": "CX_1", "company": "JPM"}}, session=sess)
    c.delay_seconds = 0
    res = c.fetch()
    assert [j.title for j in res.jobs] == ["Fund Services Intern"]
    j = c.enrich(res.jobs[0])
    assert j.url.endswith("/sites/CX_1/job/210") and "transfer agency" in j.description
    assert j.published_at.date().isoformat() == "2026-10-20" and not j.published_is_estimate

    sr = {"totalFound": 2, "content": [
        {"id": "1", "name": "Intern Credit", "location": {"city": "Luxembourg", "country": "lu"}, "releasedDate": "2026-10-23T08:00:00.000Z"},
        {"id": "2", "name": "Manager Vienna", "location": {"city": "Vienna", "country": "at"}}]}
    sess = FakeSession({r"/postings$": FakeResp(sr), r"/postings/1$": FakeResp({"jobAd": {"sections": {"jobDescription": {"text": "<p>Credit analysis</p>"}}}})})
    c = SmartRecruitersConnector({"id": "sr", "name": "BoC", "config": {"company_id": "BoC"}}, session=sess)
    c.delay_seconds = 0
    res = c.fetch()
    assert [j.title for j in res.jobs] == ["Intern Credit"]
    assert "Credit analysis" in c.enrich(res.jobs[0]).description


def test_links_row_filter_amundi_style():
    html = """<ul><li class="ts-offer-list-item"><h3><a href="/offre-de-emploi/emploi-stage-business-analyst-fund-channel-h-f_1.aspx">STAGE - Business Analyst - FUND CHANNEL H/F</a></h3>
      <ul><li>Stage</li><li>Fund Channel</li><li>Luxembourg</li></ul></li>
      <li class="ts-offer-list-item"><h3><a href="/offre-de-emploi/emploi-stage-evenementiel-h-f_2.aspx">Stage - Evénementiel H/F</a></h3>
      <ul><li>Stage</li><li>Amundi</li><li>France</li></ul></li></ul>"""
    jobs = parse_links(html, "https://jobs.amundi.com", re.compile(r"/offre-de-emploi/emploi-[^\"'#?]+_\d+\.aspx"), "amundi",
                       "Amundi", [], ["luxembourg"])
    assert len(jobs) == 1 and jobs[0].location == "Luxembourg" and jobs[0].contract == "Stage"


def test_sources_yaml_is_valid():
    from radar.connectors import REGISTRY
    srcs = yaml.safe_load((ROOT / "config/sources.yaml").read_text())["sources"]
    ids = [s["id"] for s in srcs]
    assert len(ids) == len(set(ids)) and len(ids) >= 30
    assert all(s["kind"] in REGISTRY for s in srcs)
