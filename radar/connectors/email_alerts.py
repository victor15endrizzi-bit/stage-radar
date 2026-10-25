"""Alertes e-mail des job boards (LinkedIn, jobs.lu, Indeed, JobTeaser, Welcome to the Jungle...).

Pourquoi : LinkedIn et Indeed interdisent la collecte automatisée de leurs pages, mais
t'envoient légalement des alertes par e-mail. On crée ces alertes vers une boîte Gmail
dédiée, et le radar lit cette boîte (IMAP + mot de passe d'application Gmail).
Les e-mails ne sont ni supprimés ni marqués comme lus.

Variables d'environnement : GMAIL_ADDRESS, GMAIL_APP_PASSWORD.
Limite : la rapidité dépend de la fréquence d'envoi choisie dans chaque alerte.
"""
from __future__ import annotations

import email
import imaplib
import os
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header, make_header
from email.utils import parsedate_to_datetime
from urllib.parse import parse_qs, unquote, urlparse

from bs4 import BeautifulSoup

from ..models import RawJob, SourceResult
from .base import Connector

KNOWN_SENDERS = ("linkedin", "jobs.lu", "indeed", "jobteaser", "welcometothejungle", "moovijob",
                 "glassdoor", "stepstone", "monster", "eures", "adem", "talent.com", "jobup")

RULES = [
    # (nom, regex sur l'URL, fonction -> id canonique)
    ("linkedin", re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)"), lambda m: f"https://www.linkedin.com/jobs/view/{m.group(1)}"),
    ("indeed", re.compile(r"indeed\.[a-z.]+/.*?[?&](?:jk|vjk)=([0-9a-f]+)"), lambda m: f"https://lu.indeed.com/viewjob?jk={m.group(1)}"),
    ("jobteaser", re.compile(r"jobteaser\.com/[a-z]{2}/job-offers/([0-9a-f-]{36}[^?\s\"']*)"), lambda m: f"https://www.jobteaser.com/fr/job-offers/{m.group(1)}"),
    ("jobs.lu", re.compile(r"(https?://[a-z]+\.jobs\.lu/[^?\s\"']*?(?:job|emploi|offre)[^?\s\"']*?\d{4,}[^?\s\"']*)"), lambda m: m.group(1)),
    ("wttj", re.compile(r"(https://www\.welcometothejungle\.com/[a-z]{2}/companies/[^/]+/jobs/[^?\s\"']+)"), lambda m: m.group(1)),
    ("moovijob", re.compile(r"(https://(?:www|en|de)\.moovijob\.com/(?:offres-emploi|job-offers|stellenangebote)/(?!jobs-lux)[^/]+/[^?\s\"'/]+)"), lambda m: m.group(1)),
]

NOISE = re.compile(r"\b(unsubscribe|désabonn\w*|desabonn\w*|preferences|préférences|settings|paramètres|help|aide|"
                   r"privacy|confidentialit\w*|see all( jobs)?|voir toutes|view all|manage alerts?|gérer|download|"
                   r"app store|google play)\b", re.I)


def unwrap(url: str) -> str:
    """Les liens des e-mails passent souvent par un redirecteur : on récupère l'URL cible."""
    p = urlparse(url)
    q = parse_qs(p.query)
    for key in ("url", "u", "redirect", "target", "dest", "destination"):
        if key in q and q[key] and q[key][0].startswith("http"):
            return unquote(q[key][0])
    return url


def card_text(a) -> list[str]:
    """Texte du « bloc » autour du lien (titre, entreprise, lieu)."""
    node = a
    for _ in range(5):
        if node.parent is None:
            break
        node = node.parent
        txt = node.get_text("\n", strip=True)
        if 30 < len(txt) < 400:
            break
    return [l.strip() for l in node.get_text("\n", strip=True).splitlines() if l.strip()][:6]


def parse_alert_html(html: str, source_id: str, sent_at: datetime | None = None) -> list[RawJob]:
    soup = BeautifulSoup(html, "lxml")
    found: dict[str, RawJob] = {}
    for a in soup.find_all("a", href=True):
        url = unwrap(a["href"])
        text = a.get_text(" ", strip=True)
        if NOISE.search(text or ""):
            continue
        for name, rx, canon in RULES:
            m = rx.search(url)
            if not m:
                continue
            ext = canon(m)
            lines = card_text(a)
            title = text if text and len(text) > 6 else (lines[0] if lines else "")
            if not title or NOISE.search(title):
                break
            rest = [l for l in lines if l != title]
            company = rest[0] if rest else ""
            location = rest[1] if len(rest) > 1 else ""
            if " · " in company:
                company, location = [x.strip() for x in company.split(" · ", 1)]
            prev = found.get(ext)
            if prev is None or len(title) > len(prev.title):
                found[ext] = RawJob(source_id=source_id, external_id=ext, url=ext, title=title[:200],
                                    company=company[:120], location=location[:120], published_at=sent_at,
                                    published_is_estimate=True, extra={"via": name, "needs_detail": False})
            break
    return list(found.values())


class EmailAlertsConnector(Connector):
    kind = "email"

    def fetch(self) -> SourceResult:
        addr, pwd = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD")
        if not addr or not pwd:
            raise RuntimeError("GMAIL_ADDRESS / GMAIL_APP_PASSWORD non configurés (secrets GitHub).")
        since = (datetime.now(timezone.utc) - timedelta(days=int(self.config.get("lookback_days", 3)))).strftime("%d-%b-%Y")
        jobs: dict[str, RawJob] = {}
        with imaplib.IMAP4_SSL(self.config.get("imap_host", "imap.gmail.com")) as M:
            M.login(addr, pwd)
            M.select(self.config.get("folder", "INBOX"), readonly=True)
            typ, data = M.search(None, f'(SINCE "{since}")')
            for num in (data[0].split() if data and data[0] else [])[-300:]:
                typ, msg_data = M.fetch(num, "(BODY.PEEK[])")
                msg = email.message_from_bytes(msg_data[0][1])
                sender = str(make_header(decode_header(msg.get("From", "")))).lower()
                if not any(k in sender for k in KNOWN_SENDERS):
                    continue
                try:
                    sent = parsedate_to_datetime(msg.get("Date"))
                except (TypeError, ValueError):
                    sent = None
                html = _html_part(msg)
                for j in parse_alert_html(html, self.source["id"], sent):
                    jobs.setdefault(j.external_id, j)
        return SourceResult(jobs=list(jobs.values()), complete=False)

    def enrich(self, job: RawJob) -> RawJob:
        # On ne lit pas les pages LinkedIn/Indeed (CGU) : on garde les infos de l'e-mail.
        if job.extra.get("via") in ("moovijob", "wttj", "jobs.lu"):
            try:
                from .base import enrich_from_page
                enrich_from_page(self, job)
            except Exception:  # noqa: BLE001 - l'e-mail suffit
                pass
        return job


def _html_part(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                return part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                txt = part.get_payload(decode=True).decode(part.get_content_charset() or "utf-8", "replace")
                return "".join(f'<a href="{u}">{u}</a><br>' for u in re.findall(r"https?://\S+", txt))
        return ""
    payload = msg.get_payload(decode=True) or b""
    return payload.decode(msg.get_content_charset() or "utf-8", "replace")
