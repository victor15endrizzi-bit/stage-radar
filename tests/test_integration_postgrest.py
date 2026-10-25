"""Test d'intégration : le vrai SupabaseStore contre un Postgres + PostgREST locaux
(PostgREST est le moteur d'API REST de Supabase). Ignoré si POSTGREST_URL n'est pas défini.

    POSTGREST_URL=http://localhost:3555 POSTGREST_JWT=... pytest tests/test_integration_postgrest.py
"""
import os
from datetime import timedelta

import pytest

from radar import cli
from radar.pipeline import Runner, send_daily_digest, send_pending_alerts
from radar.store import SupabaseStore
from tests.test_radar import NOW, SETTINGS, FakeConnector, FakeTG, J

pytestmark = pytest.mark.skipif(not os.environ.get("POSTGREST_URL"), reason="PostgREST local non lancé")


@pytest.fixture
def store(monkeypatch):
    import radar.pipeline as pl
    monkeypatch.setattr(pl, "make_connector", lambda s, session=None: FakeConnector(s))
    st = SupabaseStore(url="http://unused", key=os.environ["POSTGREST_JWT"])
    st.url = os.environ["POSTGREST_URL"]
    for t, k in [("status_history", "id"), ("job_links", "id"), ("source_runs", "id"), ("jobs", "id"),
                 ("sources", "id"), ("settings", "key")]:  # base propre
        st.s.delete(f"{st.url}/{t}", params={k: "not.is.null"}).raise_for_status()
    return st


def test_full_cycle_on_real_rest_api(store, tmp_path, monkeypatch):
    cli.cmd_seed(store, force=False)
    assert store.get_setting("profile")["thresholds"]["priority"] == 60
    srcs = {s["id"]: s for s in store.list_sources()}
    assert "moovijob-stages" in srcs and srcs["email-alerts"]["enabled"] is False
    for sid in srcs:
        store.update_source(sid, {"enabled": sid in ("moovijob-stages", "wd-deutsche-bank-dws"),
                                  "last_success_at": "2026-01-01T00:00:00+00:00"})
    FakeConnector.batches = {
        "moovijob-stages": [J("m1", "Stage - Product Management (H/F)", "DWS", "UCITS fund product, Master"),
                            J("m2", "Stagiaire RH", "POST", "recrutement")],
        "wd-deutsche-bank-dws": [J("/job/R1", "Product Management Intern", "DWS", "UCITS fund product, Master, CFA")],
    }
    tg = FakeTG()
    r = Runner(store, SETTINGS, tg, now=NOW).run()
    assert (r["new_jobs"], r["merged"], r["errors"]) == (2, 1, 0)
    assert send_pending_alerts(store, SETTINGS, tg, now=NOW) == 1
    pm = store.select("jobs", eq={"company_norm": "dws"})[0]
    assert pm["notified_at"] and pm["analysis"]["details"] and len(store.select("job_links", eq={"job_id": pm["id"]})) == 2
    assert store.select("status_history", eq={"job_id": pm["id"]})[0]["status"] == "nouvelle"  # trigger Postgres

    # suivi de candidature : le trigger date l'envoi et programme la relance
    store.update_job(pm["id"], {"status": "envoyee", "feedback": "top"})
    pm = store.select("jobs", eq={"id": pm["id"]})[0]
    assert pm["applied_at"] and pm["next_action"] == "Relancer"

    # paramètres modifiés depuis le tableau de bord -> recalcul automatique au passage suivant
    prof = store.get_setting("profile")
    prof["modifiers"]["internship_bonus"] = 0
    store.set_setting("profile", prof)
    cli.maybe_rescore(store, prof)
    assert store.select("jobs", eq={"id": pm["id"]})[0]["score"] != pm["score"]

    # expiration : l'offre disparaît des deux sources, 2 passages de suite
    FakeConnector.batches = {"moovijob-stages": [J("m2", "Stagiaire RH", "POST", "recrutement")], "wd-deutsche-bank-dws": []}
    for h in (1, 2):
        Runner(store, prof, tg, now=NOW + timedelta(hours=h)).run(force=True)
    assert store.select("jobs", eq={"id": pm["id"]})[0]["active"] is False

    txt = send_daily_digest(store, prof, tg, now=NOW + timedelta(days=11))
    assert "Rappels" in txt and "Relancer" in txt

    # sauvegarde chiffrée puis restauration
    monkeypatch.setenv("BACKUP_KEY", "cle-de-test")
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    cli.cmd_backup(store)
    f = next((tmp_path / "backups").glob("*.enc"))
    assert b"Product Management" not in f.read_bytes()  # chiffré
    store.update_job(pm["id"], {"notes": "modifié après sauvegarde"})
    cli.cmd_restore(store, f)
    assert store.select("jobs", eq={"id": pm["id"]})[0]["notes"] in (None, "")
