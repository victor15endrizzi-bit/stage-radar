"""Accès aux données.

Deux implémentations des mêmes primitives :
- SupabaseStore : base Postgres hébergée gratuitement (production), via l'API REST de Supabase
- MemoryStore   : en mémoire, pour les tests
Toute la logique « métier » est dans la classe de base et ne dépend pas du stockage.
"""
from __future__ import annotations

import copy
import itertools
import json
import os
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import requests

TABLES = ["settings", "sources", "jobs", "job_links", "status_history", "source_runs", "reminders"]


def iso(d: Optional[datetime]) -> Optional[str]:
    return d.astimezone(timezone.utc).isoformat() if d else None


class BaseStore:
    # ---- primitives à implémenter
    def select(self, table: str, eq: Optional[dict] = None, is_null: Iterable[str] = (), not_null: Iterable[str] = (),
               in_: Optional[tuple[str, list]] = None, order: Optional[str] = None, limit: Optional[int] = None,
               gte: Optional[dict] = None) -> list[dict]:
        raise NotImplementedError

    def insert(self, table: str, rows: list[dict], upsert_on: Optional[str] = None) -> list[dict]:
        raise NotImplementedError

    def update(self, table: str, fields: dict, eq: Optional[dict] = None, in_: Optional[tuple[str, list]] = None) -> None:
        raise NotImplementedError

    # ---- paramètres
    def get_setting(self, key: str, default: Any = None) -> Any:
        rows = self.select("settings", eq={"key": key})
        return rows[0]["value"] if rows else default

    def set_setting(self, key: str, value: Any) -> None:
        self.insert("settings", [{"key": key, "value": value, "updated_at": iso(datetime.now(timezone.utc))}], upsert_on="key")

    # ---- sources
    def list_sources(self) -> list[dict]:
        return self.select("sources", order="id")

    def update_source(self, sid: str, fields: dict) -> None:
        self.update("sources", fields, eq={"id": sid})

    # ---- liens offre <-> source
    def links_for_source(self, sid: str) -> dict[str, dict]:
        return {l["external_id"]: l for l in self.select("job_links", eq={"source_id": sid})}

    def touch_links(self, ids: list[int], now: datetime) -> None:
        for chunk in _chunks(ids, 150):
            self.update("job_links", {"last_seen_at": iso(now), "missing_runs": 0, "active": True}, in_=("id", chunk))

    def mark_missing(self, links: list[dict], now: datetime, threshold: int = 2) -> list[int]:
        """Incrémente le compteur d'absence ; renvoie les job_id dont un lien vient d'expirer."""
        expired_jobs = []
        for l in links:
            n = int(l.get("missing_runs") or 0) + 1
            fields = {"missing_runs": n}
            if n >= threshold and l.get("active", True):
                fields["active"] = False
                if l.get("job_id"):
                    expired_jobs.append(l["job_id"])
            self.update("job_links", fields, eq={"id": l["id"]})
        return expired_jobs

    def refresh_job_activity(self, job_ids: list[int], now: datetime) -> list[int]:
        """Une offre expire quand TOUTES ses sources ne l'affichent plus."""
        expired = []
        for jid in set(job_ids):
            links = self.select("job_links", eq={"job_id": jid})
            if links and not any(l.get("active", True) for l in links):
                self.update("jobs", {"active": False, "expired_at": iso(now)}, eq={"id": jid})
                expired.append(jid)
        return expired

    # ---- offres
    def find_job_by_key(self, key: str) -> Optional[dict]:
        rows = self.select("jobs", eq={"dedup_key": key})
        return rows[0] if rows else None

    def jobs_by_company(self, cn: str) -> list[dict]:
        return self.select("jobs", eq={"company_norm": cn}) if cn else []

    def insert_job(self, row: dict) -> dict:
        return self.insert("jobs", [row])[0]

    def insert_link(self, row: dict) -> dict:
        return self.insert("job_links", [row], upsert_on="source_id,external_id")[0]

    def update_job(self, jid: int, fields: dict) -> None:
        self.update("jobs", fields, eq={"id": jid})

    def pending_alerts(self) -> list[dict]:
        return self.select("jobs", eq={"active": True}, is_null=["notified_at", "digest_sent_at"], order="-score")

    def company_bias(self, step: int, cap: int) -> dict[str, int]:
        bias: dict[str, int] = {}
        for j in self.select("jobs", not_null=["feedback"]):
            v = {"like": 1, "top": 2, "dislike": -1}.get(j["feedback"], 0)
            bias[j.get("company_norm") or ""] = bias.get(j.get("company_norm") or "", 0) + v * step
        return {k: max(-cap, min(cap, v)) for k, v in bias.items()}

    def log_run(self, row: dict) -> None:
        self.insert("source_runs", [row])

    def dump(self) -> dict:
        return {t: self.select(t) for t in TABLES}


def _chunks(seq: list, n: int):
    it = iter(seq)
    while chunk := list(itertools.islice(it, n)):
        yield chunk


# ============================================================================ mémoire (tests)
class MemoryStore(BaseStore):
    PK = {"settings": "key", "sources": "id"}

    def __init__(self):
        self.t: dict[str, list[dict]] = {t: [] for t in TABLES}
        self._ids = itertools.count(1)

    def _match(self, r, eq, is_null, not_null, in_, gte):
        if eq and any(r.get(k) != v for k, v in eq.items()):
            return False
        if any(r.get(k) is not None for k in is_null):
            return False
        if any(r.get(k) is None for k in not_null):
            return False
        if in_ and r.get(in_[0]) not in in_[1]:
            return False
        if gte and any((r.get(k) or "") < v for k, v in gte.items()):
            return False
        return True

    def select(self, table, eq=None, is_null=(), not_null=(), in_=None, order=None, limit=None, gte=None):
        rows = [copy.deepcopy(r) for r in self.t[table] if self._match(r, eq, is_null, not_null, in_, gte)]
        if order:
            desc = order.startswith("-")
            k = order.lstrip("-")
            rows.sort(key=lambda r: (r.get(k) is None, r.get(k)), reverse=desc)
        return rows[:limit] if limit else rows

    def insert(self, table, rows, upsert_on=None):
        out = []
        for row in rows:
            row = copy.deepcopy(row)
            if upsert_on:
                keys = upsert_on.split(",")
                for existing in self.t[table]:
                    if all(existing.get(k) == row.get(k) for k in keys):
                        existing.update(row)
                        out.append(copy.deepcopy(existing))
                        break
                else:
                    self._new(table, row)
                    out.append(copy.deepcopy(row))
            else:
                self._new(table, row)
                out.append(copy.deepcopy(row))
        return out

    def _new(self, table, row):
        if table not in self.PK and "id" not in row:
            row["id"] = next(self._ids)
        defaults = {"jobs": {"active": True, "status": "nouvelle", "favorite": False},
                    "job_links": {"active": True, "missing_runs": 0}}.get(table, {})
        for k, v in defaults.items():
            row.setdefault(k, v)
        self.t[table].append(row)

    def update(self, table, fields, eq=None, in_=None):
        for r in self.t[table]:
            if self._match(r, eq, (), (), in_, None):
                r.update(copy.deepcopy(fields))


# ============================================================================ Supabase
class SupabaseStore(BaseStore):
    def __init__(self, url: Optional[str] = None, key: Optional[str] = None):
        self.url = (url or os.environ["SUPABASE_URL"]).rstrip("/") + "/rest/v1"
        key = key or os.environ["SUPABASE_SERVICE_KEY"]
        self.s = requests.Session()
        self.s.headers.update({"apikey": key, "Content-Type": "application/json"})
        if not key.startswith("sb_"):
            # anciennes clés (JWT « service_role ») : aussi en Authorization.
            # Les nouvelles clés « sb_secret_… » ne sont pas des JWT : seul l'en-tête apikey est attendu.
            self.s.headers["Authorization"] = f"Bearer {key}"

    @staticmethod
    def _val(v):
        if isinstance(v, bool):
            return "true" if v else "false"
        return str(v)

    def _params(self, eq=None, is_null=(), not_null=(), in_=None, gte=None):
        p = []
        for k, v in (eq or {}).items():
            p.append((k, f"eq.{self._val(v)}"))
        for k in is_null:
            p.append((k, "is.null"))
        for k in not_null:
            p.append((k, "not.is.null"))
        if in_:
            vals = ",".join(json.dumps(x) if isinstance(x, str) else str(x) for x in in_[1])
            p.append((in_[0], f"in.({vals})"))
        for k, v in (gte or {}).items():
            p.append((k, f"gte.{v}"))
        return p

    def select(self, table, eq=None, is_null=(), not_null=(), in_=None, order=None, limit=None, gte=None):
        params = self._params(eq, is_null, not_null, in_, gte) + [("select", "*")]
        if order:
            params.append(("order", f"{order.lstrip('-')}.{'desc' if order.startswith('-') else 'asc'}.nullslast"))
        out, offset, page = [], 0, 1000
        while True:
            hdr = {"Range-Unit": "items", "Range": f"{offset}-{offset + page - 1}"}
            r = self.s.get(f"{self.url}/{table}", params=params, headers=hdr, timeout=30)
            r.raise_for_status()
            batch = r.json()
            out.extend(batch)
            if len(batch) < page or (limit and len(out) >= limit):
                break
            offset += page
        return out[:limit] if limit else out

    def insert(self, table, rows, upsert_on=None):
        if not rows:
            return []
        hdr = {"Prefer": "return=representation" + (",resolution=merge-duplicates" if upsert_on else "")}
        params = {"on_conflict": upsert_on} if upsert_on else None
        r = self.s.post(f"{self.url}/{table}", params=params, data=json.dumps(rows, default=str), headers=hdr, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase insert {table}: {r.status_code} {r.text[:300]}")
        return r.json()

    def update(self, table, fields, eq=None, in_=None):
        r = self.s.patch(f"{self.url}/{table}", params=self._params(eq, in_=in_), data=json.dumps(fields, default=str),
                         headers={"Prefer": "return=minimal"}, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"Supabase update {table}: {r.status_code} {r.text[:300]}")
