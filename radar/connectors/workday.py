"""Sites carrière Workday (*.myworkdayjobs.com).

Méthode : l'API JSON publique utilisée par la page carrière elle-même
(POST /wday/cxs/{tenant}/{site}/jobs), sans compte ni clé.
Avantage : une offre y apparaît en général AVANT les job boards.
Limite : chaque entreprise doit être ajoutée (host, tenant, site) ; pour en ajouter une,
ouvre son site carrière : l'URL ressemble à https://{tenant}.wd3.myworkdayjobs.com/{site}.
"""
from __future__ import annotations

import re

from ..models import RawJob, SourceResult
from ..textutil import extract_languages, html_to_text, norm, parse_date_any, parse_relative_fr
from .base import Connector

PAGE = 20
MAX_PER_SEARCH = 200


class WorkdayConnector(Connector):
    kind = "workday"
    delay_seconds = 1.0

    @property
    def api(self) -> str:
        c = self.config
        return f"https://{c['host']}/wday/cxs/{c['tenant']}/{c['site']}"

    def public_url(self, external_path: str) -> str:
        c = self.config
        return f"https://{c['host']}/en-US/{c['site']}{external_path}"

    def fetch(self) -> SourceResult:
        jobs: dict[str, RawJob] = {}
        must = [norm(x) for x in self.config.get("location_must_contain", [])]
        complete = True
        for q in self.config.get("searches", [""]):
            offset, total = 0, None
            while True:
                r = self.post(f"{self.api}/jobs", json={"appliedFacets": {}, "limit": PAGE, "offset": offset, "searchText": q},
                              headers={"Accept": "application/json", "Content-Type": "application/json"})
                data = r.json()
                if total is None:
                    total = int(data.get("total") or 0)
                postings = data.get("jobPostings") or []
                for p in postings:
                    path = p.get("externalPath")
                    if not path or path in jobs:
                        continue
                    loc = p.get("locationsText") or ""
                    multi = bool(re.match(r"\d+\s+locations?", loc, re.I))
                    if must and not multi and not any(m in norm(loc) for m in must):
                        continue
                    jobs[path] = RawJob(
                        source_id=self.source["id"], external_id=path, url=self.public_url(path),
                        title=p.get("title", ""), company=self.source.get("company") or self.source["name"].split("(")[0].strip(),
                        location=loc, published_at=parse_relative_fr(p.get("postedOn", "")),
                        published_is_estimate=True,
                        extra={"check_location": multi},
                    )
                offset += PAGE
                if not postings or offset >= total:
                    break
                if offset >= MAX_PER_SEARCH:
                    complete = False
                    break
        return SourceResult(jobs=list(jobs.values()), complete=complete)

    def enrich(self, job: RawJob) -> RawJob:
        r = self.get(f"{self.api}{job.external_id}", headers={"Accept": "application/json"})
        info = (r.json() or {}).get("jobPostingInfo") or {}
        job.title = info.get("title") or job.title
        job.description = html_to_text(info.get("jobDescription", ""))
        locs = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
        job.location = "; ".join(l for l in locs if l) or job.location
        if info.get("startDate"):
            d = parse_date_any(info["startDate"])
            if d:
                job.published_at, job.published_is_estimate = d, False
        if info.get("externalUrl"):
            job.url = info["externalUrl"]
        job.contract = info.get("timeType") or job.contract
        job.languages = extract_languages(job.description)
        must = [norm(x) for x in self.config.get("location_must_contain", [])]
        if job.extra.get("check_location") and must and not any(m in norm(job.location) for m in must):
            job.extra["drop"] = "hors zone"
        return job
