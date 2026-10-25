"""Orchestration : collecte -> dédoublonnage -> scoring -> stockage -> notifications."""
from __future__ import annotations

import logging
import traceback
from datetime import datetime, timedelta, timezone
from typing import Optional

from rapidfuzz import fuzz

from .connectors import make_connector
from .models import RawJob
from .notify import PARIS, Telegram, esc, fmt_dt, format_job_message, job_buttons, send_email
from .scoring import analyze
from .store import BaseStore, iso
from .textutil import title_key

log = logging.getLogger(__name__)
FUZZY_THRESHOLD = 90
MAX_ALERTS_PER_RUN = 20


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(v) -> Optional[datetime]:
    if not v:
        return None
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v).replace("Z", "+00:00"))


def is_due(src: dict, now: datetime) -> bool:
    last = parse_ts(src.get("last_run_at"))
    if not last:
        return True
    # petite marge : le planificateur GitHub n'est pas précis à la minute
    return (now - last) >= timedelta(minutes=max(5, int(src.get("interval_minutes") or 30) - 4))


# ------------------------------------------------------------------------------- collecte
class Runner:
    def __init__(self, store: BaseStore, settings: dict, telegram: Optional[Telegram] = None,
                 session=None, now: Optional[datetime] = None):
        self.store, self.settings = store, settings
        self.tg = telegram or Telegram()
        self.session = session
        self.now = now or now_utc()
        mods = settings.get("modifiers", {})
        self.bias = store.company_bias(mods.get("feedback_company_step", 6), mods.get("feedback_company_max", 18))
        self.report: list[str] = []

    def run(self, only: Optional[str] = None, force: bool = False) -> dict:
        stats = {"sources": 0, "new_jobs": 0, "merged": 0, "errors": 0, "expired": 0}
        for src in self.store.list_sources():
            if only and src["id"] != only:
                continue
            if not only and (not src.get("enabled", True) or (not force and not is_due(src, self.now))):
                continue
            stats["sources"] += 1
            r = self.run_source(src)
            for k in ("new_jobs", "merged", "expired"):
                stats[k] += r.get(k, 0)
            stats["errors"] += 0 if r["ok"] else 1
        return stats

    def run_source(self, src: dict) -> dict:
        started = now_utc()
        first_run = not src.get("last_success_at")
        res = {"ok": False, "found": 0, "new_jobs": 0, "merged": 0, "expired": 0, "error": None}
        try:
            conn = make_connector(src, session=self.session)
            result = conn.fetch()
            res["found"] = len(result.jobs)
            known = self.store.links_for_source(src["id"])
            seen_link_ids = []
            for raw in result.jobs:
                link = known.get(raw.external_id)
                if link:
                    seen_link_ids.append(link["id"])
                    continue
                try:
                    raw = conn.enrich(raw)
                except Exception as e:  # noqa: BLE001 - la fiche est secondaire, on garde l'offre
                    log.warning("Fiche illisible %s : %s", raw.url, e)
                outcome = self.ingest(raw, src, initial=first_run)
                res[outcome] = res.get(outcome, 0) + 1
            self.store.touch_links(seen_link_ids, self.now)
            if result.complete:
                seen = {r.external_id for r in result.jobs}
                missing = [l for eid, l in known.items() if eid not in seen and l.get("active", True)]
                expired_jobs = self.store.mark_missing(missing, self.now)
                res["expired"] = len(self.store.refresh_job_activity(expired_jobs, self.now))
            res["ok"] = True
        except Exception as e:  # noqa: BLE001 - une source en panne ne doit pas bloquer les autres
            res["error"] = f"{type(e).__name__}: {e}"[:500]
            log.error("Source %s en erreur : %s\n%s", src["id"], e, traceback.format_exc())
        self._record(src, res, started, first_run)
        return res

    def _record(self, src, res, started, first_run):
        fields = {"last_run_at": iso(self.now), "last_status": "ok" if res["ok"] else "error",
                  "last_error": res["error"], "last_found": res["found"]}
        threshold = int(self.settings.get("notifications", {}).get("source_failure_alert_after", 3))
        if res["ok"]:
            fields.update(last_success_at=iso(self.now), consecutive_failures=0, alerted_failure=False)
            if src.get("alerted_failure"):
                self.tg.send(f"✅ Source réparée : <b>{esc(src['name'])}</b> ({res['found']} offres lues)")
        else:
            n = int(src.get("consecutive_failures") or 0) + 1
            fields["consecutive_failures"] = n
            if n >= threshold and not src.get("alerted_failure"):
                self.tg.send(f"⚠️ <b>Source en panne</b> : {esc(src['name'])}\n{n} échecs de suite.\n"
                             f"Dernière erreur : <code>{esc(res['error'])}</code>\n"
                             f"Les autres sources continuent. Vérifie la page « Sources » du tableau de bord.")
                fields["alerted_failure"] = True
        self.store.update_source(src["id"], fields)
        self.store.log_run({"source_id": src["id"], "started_at": iso(started), "finished_at": iso(now_utc()),
                            "ok": res["ok"], "found": res["found"], "new_jobs": res["new_jobs"],
                            "error": res["error"], "initial": first_run})

    # ------------------------------------------------------------------------ une nouvelle annonce
    def ingest(self, raw: RawJob, src: dict, initial: bool = False) -> str:
        link_row = {"source_id": src["id"], "external_id": raw.external_id, "url": raw.url,
                    "first_seen_at": iso(self.now), "last_seen_at": iso(self.now), "active": True, "missing_runs": 0}
        if raw.extra.get("drop"):
            self.store.insert_link({**link_row, "job_id": None, "ignored_reason": raw.extra["drop"]})
            return "ignored"
        settings = self.settings
        if src.get("target"):
            settings = {**settings, "target_companies": list(settings.get("target_companies", [])) + [raw.company]}
        a = analyze(raw, settings, self.bias, now=self.now)
        tkey = title_key(a.title)
        dkey = f"{a.company_norm}|{tkey}"
        existing = self.store.find_job_by_key(dkey) or self._fuzzy_match(a.company_norm, tkey)
        if existing:
            # même offre déjà vue sur une autre source : on ajoute juste le lien
            self.store.insert_link({**link_row, "job_id": existing["id"]})
            upd = {}
            if raw.published_at and not raw.published_is_estimate:
                prev = parse_ts(existing.get("published_at"))
                if not prev or existing.get("published_is_estimate") or raw.published_at < prev:
                    upd.update(published_at=iso(raw.published_at), published_is_estimate=False)
            if not existing.get("active", True):
                upd.update(active=True, expired_at=None)
            if len(raw.description or "") > len(existing.get("description") or "") + 200:
                upd["description"] = raw.description[:15000]
            if upd:
                self.store.update_job(existing["id"], upd)
            return "merged"
        row = {
            "dedup_key": dkey, "title": a.title, "company": a.company, "company_norm": a.company_norm,
            "location": raw.location, "contract": raw.contract, "description": (raw.description or "")[:15000],
            "url": raw.url, "published_at": iso(raw.published_at), "published_is_estimate": raw.published_is_estimate,
            "detected_at": iso(self.now), "is_internship": a.is_internship, "duration": a.duration,
            "start_date": a.start_date, "languages": a.languages, "domain": a.domain, "domain_label": a.domain_label,
            "score": a.score, "priority": a.priority, "first_source_id": src["id"],
            "analysis": {"details": a.details, "reasons": a.reasons, "missions": a.missions, "requirements": a.requirements},
            "active": True, "status": "nouvelle", "favorite": False, "initial_import": initial,
        }
        if initial:
            row["notified_at"] = iso(self.now)  # premier passage : récapitulatif au lieu de 100 alertes
        job = self.store.insert_job(row)
        self.store.insert_link({**link_row, "job_id": job["id"]})
        return "new_jobs"

    def _fuzzy_match(self, cn: str, tkey: str) -> Optional[dict]:
        if not cn or not tkey:
            return None
        best, best_score = None, 0
        for j in self.store.jobs_by_company(cn):
            s = fuzz.token_set_ratio(tkey, title_key(j["title"]))
            if s > best_score:
                best, best_score = j, s
        return best if best_score >= FUZZY_THRESHOLD else None


# ------------------------------------------------------------------------------- alertes
def in_quiet_hours(now: datetime, settings: dict) -> bool:
    q = settings.get("notifications", {}).get("quiet_hours") or []
    if len(q) != 2:
        return False
    h = now.astimezone(PARIS).hour
    start, end = int(q[0]), int(q[1])
    return (h >= start or h < end) if start > end else (start <= h < end)


def eligible_immediate(job: dict, settings: dict) -> bool:
    n = settings.get("notifications", {})
    if not job.get("is_internship") and not n.get("notify_non_internships", False):
        return False
    return int(job.get("score") or 0) >= int(n.get("immediate_min_score", 35))


def source_names(store: BaseStore, job_id: int, sources: dict) -> list[str]:
    return sorted({sources.get(l["source_id"], l["source_id"]) for l in store.select("job_links", eq={"job_id": job_id})})


def send_pending_alerts(store: BaseStore, settings: dict, tg: Telegram, now: Optional[datetime] = None) -> int:
    now = now or now_utc()
    if not tg.enabled or in_quiet_hours(now, settings):
        return 0
    srcs = {s["id"]: s["name"] for s in store.list_sources()}
    todo = [j for j in store.pending_alerts() if eligible_immediate(j, settings)]
    sent = 0
    for job in todo[:MAX_ALERTS_PER_RUN]:
        job["source_names"] = source_names(store, job["id"], srcs)
        if tg.send(format_job_message(job), job_buttons(job), silent=job.get("priority") != "prioritaire"):
            store.update_job(job["id"], {"notified_at": iso(now)})
            sent += 1
    rest = todo[MAX_ALERTS_PER_RUN:]
    if rest:
        txt = f"➕ <b>{len(rest)} autres offres</b> détectées (classées dans le tableau de bord) :\n" + "\n".join(
            f"• {j['score']}/100 — <a href=\"{esc(j['url'])}\">{esc(j['title'][:70])}</a> · {esc(j.get('company'))}" for j in rest[:25])
        if tg.send(txt):
            for j in rest:
                store.update_job(j["id"], {"notified_at": iso(now)})
    return sent


def send_initial_summary(store: BaseStore, settings: dict, tg: Telegram, since: datetime) -> bool:
    jobs = [j for j in store.select("jobs", eq={"initial_import": True}, order="-score")
            if (parse_ts(j.get("detected_at")) or since) >= since and eligible_immediate(j, settings)]
    if not jobs or not tg.enabled:
        return False
    lines = [f"🎯 <b>Radar activé</b> — {len(jobs)} stages pertinents déjà en ligne. Le top 15 :"]
    for j in jobs[:15]:
        lines.append(f"• <b>{j['score']}</b> — <a href=\"{esc(j['url'])}\">{esc(j['title'][:70])}</a> · {esc(j.get('company'))}")
    lines.append("\nÀ partir de maintenant, chaque nouvelle offre t'est envoyée dès sa détection.")
    return tg.send("\n".join(lines))


def send_daily_digest(store: BaseStore, settings: dict, tg: Telegram, now: Optional[datetime] = None) -> str:
    """Récap du matin : rappels du jour, offres peu pertinentes de la veille, état des sources."""
    now = now or now_utc()
    parts = [f"☀️ <b>Récap du {now.astimezone(PARIS).strftime('%d/%m')}</b>"]
    end_today = now.astimezone(PARIS).replace(hour=23, minute=59).astimezone(timezone.utc)
    due = [j for j in store.select("jobs", not_null=["next_action_at"])
           if parse_ts(j["next_action_at"]) <= end_today and j.get("status") not in ("refus", "abandonnee", "offre")]
    if due:
        parts.append("\n⏰ <b>Rappels</b>")
        for j in due[:15]:
            parts.append(f"• {esc(j.get('next_action') or 'Relance')} — {esc(j['title'][:60])} · {esc(j.get('company'))} "
                         f"({fmt_dt(j['next_action_at'], with_time=False)})")
    low = [j for j in store.pending_alerts() if not eligible_immediate(j, settings)
           and (j.get("is_internship") or settings.get("notifications", {}).get("notify_non_internships"))]
    if low:
        parts.append(f"\n📂 <b>{len(low)} stages peu pertinents</b> (non alertés) :")
        for j in low[:12]:
            parts.append(f"• {j['score']} — <a href=\"{esc(j['url'])}\">{esc(j['title'][:60])}</a> · {esc(j.get('company'))}")
    broken = [s for s in store.list_sources() if s.get("enabled", True) and s.get("last_status") == "error"]
    if broken:
        parts.append("\n⚠️ Sources en erreur : " + ", ".join(esc(s["name"]) for s in broken))
    week = (now - timedelta(days=7)).isoformat()
    n_week = len(store.select("jobs", gte={"detected_at": week}))
    parts.append(f"\n📊 {n_week} offres détectées sur 7 jours.")
    text = "\n".join(parts)
    if tg.enabled and (due or low or broken or settings.get("notifications", {}).get("digest_enabled", True)):
        tg.send(text, silent=True)
    for j in store.pending_alerts():
        if not eligible_immediate(j, settings):
            store.update_job(j["id"], {"digest_sent_at": iso(now)})
    try:
        send_email(f"Radar stages — récap du {now.astimezone(PARIS).strftime('%d/%m')}",
                   text.replace("\n", "<br>"))
    except Exception as e:  # noqa: BLE001
        log.warning("E-mail de récap non envoyé : %s", e)
    return text
