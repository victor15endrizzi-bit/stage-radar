"""Notifications : Telegram (alertes immédiates, récap, pannes) et e-mail (récap, optionnel).

Variables d'environnement :
  TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID         (obligatoires pour les alertes)
  DASHBOARD_URL                                (bouton « Ouvrir dans le tableau de bord »)
  GMAIL_ADDRESS, GMAIL_APP_PASSWORD, DIGEST_EMAIL_TO  (récap par e-mail, optionnel)
"""
from __future__ import annotations

import html
import logging
import os
import smtplib
import time
from datetime import datetime
from email.mime.text import MIMEText
from typing import Optional
from zoneinfo import ZoneInfo

import requests

log = logging.getLogger(__name__)
PARIS = ZoneInfo("Europe/Paris")
PRIO_ICON = {"prioritaire": "🔥 PRIORITAIRE", "interessante": "⭐ Intéressante", "faible": "· Peu pertinente",
             "hors_stage": "· Hors stage"}


def esc(s) -> str:
    return html.escape(str(s or ""), quote=False)


def fmt_dt(iso_or_dt, with_time=True) -> str:
    if not iso_or_dt:
        return "?"
    d = iso_or_dt if isinstance(iso_or_dt, datetime) else datetime.fromisoformat(str(iso_or_dt).replace("Z", "+00:00"))
    d = d.astimezone(PARIS)
    return d.strftime("%d/%m %H:%M") if with_time else d.strftime("%d/%m/%Y")


def format_job_message(job: dict) -> str:
    """Message d'alerte d'une offre (HTML Telegram, < 4096 caractères)."""
    a = job.get("analysis") or {}
    lines = [
        f"<b>{PRIO_ICON.get(job.get('priority'), '')} · {job.get('score', 0)}/100</b>",
        f"<b>{esc(job['title'])}</b>",
        f"🏢 {esc(job.get('company') or '?')} — 📍 {esc(job.get('location') or 'Luxembourg')}",
    ]
    pub = job.get("published_at")
    pub_txt = (("≈ " if job.get("published_is_estimate") else "") + fmt_dt(pub, with_time=bool(job.get("published_is_estimate")))) if pub else "non indiquée"
    lines.append(f"🗓 Publiée : {pub_txt} · Détectée : {fmt_dt(job.get('detected_at'))}")
    facts = []
    if job.get("duration"):
        facts.append(f"⏱ {esc(job['duration'])}")
    if job.get("start_date"):
        facts.append(f"🚀 début {esc(job['start_date'])}")
    if job.get("languages"):
        facts.append("🗣 " + "/".join(job["languages"]))
    if facts:
        lines.append(" · ".join(facts))
    if a.get("reasons"):
        lines.append("\n✅ <b>Pourquoi</b>\n" + "\n".join(f"• {esc(r)}" for r in a["reasons"][:4]))
    if a.get("missions"):
        lines.append("\n📝 <b>Missions</b>\n" + "\n".join(f"• {esc(m[:160])}" for m in a["missions"][:3]))
    if a.get("requirements"):
        lines.append("\n📌 <b>Prérequis</b>\n" + "\n".join(f"• {esc(r[:160])}" for r in a["requirements"][:3]))
    if job.get("source_names"):
        lines.append(f"\n🔎 Source : {esc(', '.join(job['source_names']))}")
    msg = "\n".join(lines)
    return msg[:3900]


def job_buttons(job: dict) -> list[list[dict]]:
    row = [{"text": "🔗 Voir l'offre", "url": job["url"]}]
    dash = os.environ.get("DASHBOARD_URL")
    if dash:
        row.append({"text": "📋 Tableau de bord", "url": f"{dash.rstrip('/')}/#job={job['id']}"})
    return [row]


class Telegram:
    def __init__(self, token: Optional[str] = None, chat_id: Optional[str] = None):
        self.token = token or os.environ.get("TELEGRAM_BOT_TOKEN")
        self.chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID")
        self.sent = 0

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.chat_id)

    def send(self, text: str, buttons: Optional[list] = None, silent: bool = False) -> bool:
        if not self.enabled:
            log.warning("Telegram non configuré : message non envoyé")
            return False
        payload = {"chat_id": self.chat_id, "text": text, "parse_mode": "HTML",
                   "disable_web_page_preview": True, "disable_notification": silent}
        if buttons:
            payload["reply_markup"] = {"inline_keyboard": buttons}
        for attempt in range(4):
            r = requests.post(f"https://api.telegram.org/bot{self.token}/sendMessage", json=payload, timeout=20)
            if r.status_code == 429:  # limite de débit Telegram
                time.sleep(int(r.json().get("parameters", {}).get("retry_after", 3)) + 1)
                continue
            if r.ok:
                self.sent += 1
                time.sleep(1.1)  # ~1 message / seconde max vers un même chat
                return True
            log.error("Telegram %s : %s", r.status_code, r.text[:300])
            if r.status_code == 400 and "parse" in r.text.lower():  # HTML invalide -> texte brut
                payload["parse_mode"] = None
                payload["text"] = html.unescape(text.replace("<b>", "").replace("</b>", ""))
                continue
            return False
        return False


def send_email(subject: str, body_html: str) -> bool:
    addr, pwd, to = os.environ.get("GMAIL_ADDRESS"), os.environ.get("GMAIL_APP_PASSWORD"), os.environ.get("DIGEST_EMAIL_TO")
    if not (addr and pwd and to):
        return False
    msg = MIMEText(body_html, "html", "utf-8")
    msg["Subject"], msg["From"], msg["To"] = subject, addr, to
    with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
        s.login(addr, pwd)
        s.send_message(msg)
    return True
