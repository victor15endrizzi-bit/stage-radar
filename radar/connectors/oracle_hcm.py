"""Sites carrière Oracle Recruiting Cloud (« Candidate Experience »), ex. JPMorgan, Schroders, BNY.

Méthode : l'API REST publique utilisée par la page carrière elle-même
(GET /hcmRestApi/resources/latest/recruitingCEJobRequisitions), sans compte ni clé.
Avantage : date de publication fiable, description complète.
Ajouter une entreprise : l'URL de son site carrière ressemble à
https://{host}/hcmUI/CandidateExperience/en/sites/{site} -> config {host, site}.
"""
from __future__ import annotations

from ..models import RawJob, SourceResult
from ..textutil import extract_languages, html_to_text, norm, parse_date_any
from .base import Connector

PAGE = 25
MAX = 200


class OracleHCMConnector(Connector):
    kind = "oracle"
    delay_seconds = 1.0

    @property
    def base(self) -> str:
        return f"https://{self.config['host']}/hcmRestApi/resources/latest"

    def public_url(self, rid: str) -> str:
        c = self.config
        return f"https://{c['host']}/hcmUI/CandidateExperience/en/sites/{c['site']}/job/{rid}"

    def fetch(self) -> SourceResult:
        must = [norm(x) for x in self.config.get("location_must_contain", ["luxemb", "senningerberg", "munsbach"])]
        jobs: dict[str, RawJob] = {}
        complete = True
        for kw in self.config.get("searches", ["Luxembourg"]):
            offset = 0
            while True:
                finder = (f"findReqs;siteNumber={self.config['site']},limit={PAGE},offset={offset},"
                          f"keyword={kw},sortBy=POSTING_DATES_DESC")
                r = self.get(f"{self.base}/recruitingCEJobRequisitions",
                             params={"onlyData": "true", "expand": "requisitionList.secondaryLocations", "finder": finder},
                             headers={"Accept": "application/json"})
                items = (r.json().get("items") or [{}])[0]
                reqs = items.get("requisitionList") or []
                total = int(items.get("TotalJobsCount") or 0)
                for q in reqs:
                    rid = str(q.get("Id"))
                    locs = [q.get("PrimaryLocation") or ""] + [s.get("Name", "") for s in (q.get("secondaryLocations") or [])]
                    loc = "; ".join(l for l in locs if l)
                    if must and not any(m in norm(loc) for m in must):
                        continue
                    jobs.setdefault(rid, RawJob(
                        source_id=self.source["id"], external_id=rid, url=self.public_url(rid),
                        title=q.get("Title", ""), company=self.config.get("company", ""), location=loc,
                        published_at=parse_date_any(q.get("PostedDate") or ""), published_is_estimate=False,
                    ))
                offset += PAGE
                if not reqs or offset >= total:
                    break
                if offset >= MAX:
                    complete = False
                    break
        return SourceResult(jobs=list(jobs.values()), complete=complete)

    def enrich(self, job: RawJob) -> RawJob:
        finder = f'ById;Id="{job.external_id}",siteNumber={self.config["site"]}'
        r = self.get(f"{self.base}/recruitingCEJobRequisitionDetails",
                     params={"onlyData": "true", "expand": "all", "finder": finder},
                     headers={"Accept": "application/json"})
        items = r.json().get("items") or []
        if items:
            d = items[0]
            parts = [d.get("ExternalDescriptionStr"), d.get("ExternalResponsibilitiesStr"), d.get("ExternalQualificationsStr")]
            job.description = html_to_text("\n".join(p for p in parts if p))
            job.contract = d.get("JobSchedule") or d.get("WorkerType") or job.contract
        job.languages = extract_languages(job.description)
        return job
