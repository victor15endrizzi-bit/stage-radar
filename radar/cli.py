"""Point d'entrée : python -m radar <commande>

Commandes :
  run            collecte les sources dues, enregistre, envoie les alertes
  digest         récap quotidien (rappels, offres peu pertinentes, sources en erreur)
  test-notify    envoie un message de test + une fausse offre sur Telegram (et e-mail si configuré)
  seed           copie config/*.yaml dans la base (sans écraser tes réglages, sauf --force)
  check-source   teste une source SANS rien enregistrer : python -m radar check-source moovijob-stages
  rescore        recalcule tous les scores (après un changement de paramètres)
  backup         exporte la base en fichier chiffré (backups/)
  restore        restaure un export : python -m radar restore backups/xxx.enc
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from .connectors import make_connector
from .notify import Telegram, format_job_message, job_buttons, send_email
from .pipeline import Runner, send_daily_digest, send_initial_summary, send_pending_alerts, now_utc, parse_ts
from .scoring import analyze
from .store import SupabaseStore, TABLES, iso

ROOT = Path(__file__).resolve().parent.parent
log = logging.getLogger("radar")


def load_yaml(name: str) -> dict:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))


def load_settings(store) -> dict:
    s = store.get_setting("profile") if store else None
    return s or load_yaml("profile.yaml")


def get_store():
    return SupabaseStore()


# ---------------------------------------------------------------------------- commandes
def cmd_seed(store, force=False):
    if force or not store.get_setting("profile"):
        store.set_setting("profile", load_yaml("profile.yaml"))
        print("Paramètres copiés depuis config/profile.yaml")
    existing = {s["id"]: s for s in store.list_sources()}
    rows = []
    for s in load_yaml("sources.yaml")["sources"]:
        row = {"id": s["id"], "name": s["name"], "kind": s["kind"], "config": s.get("config", {}),
               "interval_minutes": s.get("interval_minutes", 30), "target": bool(s.get("target", False))}
        if s["id"] not in existing:
            row["enabled"] = s.get("enabled", True)
            rows.append(row)
        elif force:
            rows.append(row)  # met à jour la config mais garde enabled / historique
    if rows:
        store.insert("sources", rows, upsert_on="id")
    print(f"{len(rows)} source(s) ajoutée(s)/mise(s) à jour.")


def cmd_run(store, only=None, force=False):
    settings = load_settings(store)
    maybe_rescore(store, settings)
    tg = Telegram()
    runner = Runner(store, settings, tg)
    stats = runner.run(only=only, force=force)
    initial = [r for r in store.select("source_runs", gte={"started_at": iso(runner.now)}) if r.get("initial") and r.get("ok")]
    if initial:
        send_initial_summary(store, settings, tg, since=runner.now)
    stats["alerts"] = send_pending_alerts(store, settings, tg)
    print(json.dumps(stats, ensure_ascii=False))
    return stats


def maybe_rescore(store, settings):
    prof = next(iter(store.select("settings", eq={"key": "profile"})), None)
    last = store.get_setting("last_rescore")
    if prof and prof.get("updated_at") and (not last or str(prof["updated_at"]) > str(last)):
        cmd_rescore(store, settings)
    elif store.select("jobs", is_null=["score"], limit=1):  # offres ajoutées à la main
        cmd_rescore(store, settings, only_missing=True)


def cmd_rescore(store, settings=None, only_missing=False):
    from .models import RawJob
    settings = settings or load_settings(store)
    mods = settings.get("modifiers", {})
    bias = store.company_bias(mods.get("feedback_company_step", 6), mods.get("feedback_company_max", 18))
    targets = {s["id"] for s in store.list_sources() if s.get("target")}
    n = 0
    for j in store.select("jobs", is_null=["score"]) if only_missing else store.select("jobs"):
        raw = RawJob(source_id=j.get("first_source_id") or "", external_id="", url=j["url"], title=j["title"],
                     company=j.get("company") or "", location=j.get("location") or "", description=j.get("description") or "",
                     contract=j.get("contract") or "", published_at=parse_ts(j.get("published_at")),
                     published_is_estimate=bool(j.get("published_is_estimate")), languages=j.get("languages") or [],
                     is_internship_hint=j.get("is_internship"))
        st = settings
        if j.get("first_source_id") in targets:
            st = {**settings, "target_companies": list(settings.get("target_companies", [])) + [raw.company]}
        a = analyze(raw, st, bias, now=parse_ts(j.get("detected_at")))
        if a.score != j.get("score") or a.priority != j.get("priority"):
            store.update_job(j["id"], {"score": a.score, "priority": a.priority, "domain": a.domain,
                                       "domain_label": a.domain_label,
                                       "analysis": {**(j.get("analysis") or {}), "details": a.details, "reasons": a.reasons}})
            n += 1
    if not only_missing:
        store.set_setting("last_rescore", iso(now_utc()))
    print(f"{n} score(s) mis à jour.")


def cmd_test_notify(store):
    tg = Telegram()
    if not tg.enabled:
        print("ERREUR : TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID manquants.")
        return 1
    ok1 = tg.send("✅ <b>Test du radar</b> : les notifications fonctionnent.\n"
                  f"Envoyé le {datetime.now().strftime('%d/%m/%Y %H:%M')} (UTC) depuis GitHub Actions.")
    best = None
    if store:
        rows = store.select("jobs", eq={"active": True}, order="-score", limit=1)
        best = rows[0] if rows else None
    demo = best or {
        "id": 0, "title": "[EXEMPLE] Internship — Multi-Asset Portfolio Management", "company": "Exemple AM",
        "location": "Luxembourg", "score": 82, "priority": "prioritaire", "url": "https://www.moovijob.com",
        "published_at": iso(now_utc()), "detected_at": iso(now_utc()), "duration": "6 mois", "start_date": "mai 2027",
        "languages": ["EN", "FR"], "analysis": {"reasons": ["Métier — Gestion d'actifs — front (titre : portfolio manag)"],
                                              "missions": ["Support portfolio managers in asset allocation"],
                                              "requirements": ["Master in Finance, CFA candidate"]}}
    if best:
        demo["title"] = "[TEST] " + demo["title"]
    ok2 = tg.send(format_job_message(demo), job_buttons(demo))
    mail = False
    try:
        mail = send_email("Radar stages — test", "<b>Test</b> : le récap par e-mail fonctionne.")
    except Exception as e:  # noqa: BLE001
        print("E-mail : échec —", e)
    print(f"Telegram : {'OK' if ok1 and ok2 else 'ÉCHEC'} · E-mail : {'OK' if mail else 'non configuré / échec'}")
    return 0 if ok1 and ok2 else 1


def cmd_check_source(store, sid):
    """Diagnostic : lit une source et affiche ce qui serait détecté, sans rien enregistrer."""
    if store:
        src = next((s for s in store.list_sources() if s["id"] == sid), None)
    else:
        src = None
    if not src:
        src = next((s for s in load_yaml("sources.yaml")["sources"] if s["id"] == sid), None)
    if not src:
        print("Source inconnue :", sid)
        return 1
    settings = load_settings(store)
    conn = make_connector(src)
    res = conn.fetch()
    print(f"{src['name']} : {len(res.jobs)} offres (liste complète : {res.complete})")
    for raw in res.jobs[:5]:
        try:
            raw = conn.enrich(raw)
        except Exception as e:  # noqa: BLE001
            print("  fiche illisible :", e)
        a = analyze(raw, settings)
        print(f"- [{a.score:3d} {a.priority}] {a.title} · {raw.company} · {raw.location} · publiée {raw.published_at} "
              f"· {a.duration or ''} {a.start_date or ''} · langues {a.languages}\n    {raw.url}\n    "
              + " | ".join(f"{d['points']:+d} {d['label']}" for d in a.details))
    for raw in res.jobs[5:40]:
        print(f"  · {raw.title} — {raw.company}")
    return 0


def _fernet():
    from cryptography.fernet import Fernet
    key = os.environ.get("BACKUP_KEY")
    if not key:
        raise SystemExit("BACKUP_KEY manquant (secret GitHub).")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def cmd_backup(store):
    data = json.dumps(store.dump(), ensure_ascii=False, default=str).encode()
    out = ROOT / "backups"
    out.mkdir(exist_ok=True)
    path = out / f"radar-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M')}.enc"
    path.write_bytes(_fernet().encrypt(data))
    print("Sauvegarde chiffrée :", path)


def cmd_restore(store, path):
    data = json.loads(_fernet().decrypt(Path(path).read_bytes()))
    keys = {"settings": "key", "sources": "id", "jobs": "id", "job_links": "id", "status_history": "id",
            "source_runs": "id", "reminders": "id"}
    for t in TABLES:
        rows = data.get(t) or []
        for i in range(0, len(rows), 200):
            store.insert(t, rows[i:i + 200], upsert_on=keys[t])
        print(f"{t} : {len(rows)} ligne(s) restaurée(s)")


def main(argv=None):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(prog="radar")
    sub = p.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--source")
    r.add_argument("--force", action="store_true")
    sub.add_parser("digest")
    sub.add_parser("test-notify")
    s = sub.add_parser("seed")
    s.add_argument("--force", action="store_true")
    c = sub.add_parser("check-source")
    c.add_argument("source_id")
    c.add_argument("--no-db", action="store_true")
    sub.add_parser("rescore")
    sub.add_parser("backup")
    rs = sub.add_parser("restore")
    rs.add_argument("path")
    a = p.parse_args(argv)

    if a.cmd == "check-source":
        store = None if a.no_db or not os.environ.get("SUPABASE_URL") else get_store()
        return cmd_check_source(store, a.source_id)
    if a.cmd == "test-notify":
        store = get_store() if os.environ.get("SUPABASE_URL") else None
        return cmd_test_notify(store)
    store = get_store()
    if a.cmd == "seed":
        cmd_seed(store, a.force)
    elif a.cmd == "run":
        cmd_run(store, a.source, a.force)
    elif a.cmd == "digest":
        send_daily_digest(store, load_settings(store), Telegram())
    elif a.cmd == "rescore":
        cmd_rescore(store)
    elif a.cmd == "backup":
        cmd_backup(store)
    elif a.cmd == "restore":
        cmd_restore(store, a.path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
