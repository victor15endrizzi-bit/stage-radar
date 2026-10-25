"""SmartRecruiters — API publique officielle (api.smartrecruiters.com/v1/companies/{id}/postings).

Ajouter une entreprise : son site carrière est https://careers.smartrecruiters.com/{id} -> config {company_id}.
"""
from __future__ import annotations

from ..models import RawJob, SourceResult
from ..textutil import extract_languages, html_to_text, norm, parse_date_any
from .base import Connector

API = "https://api.smartrecruiters.com/v1/companies"


class SmartRecruitersConnector(Connector):
    kind = "smartrecruiters"
    delay_seconds = 1.0

    def fetch(self) -> SourceResult:
        cid = self.config["company_id"]
        must = [norm(x) for x in self.config.get("location_must_contain", ["luxemb"])]
        jobs, offset = {}, 0
        while True:
            d = self.get(f"{API}/{cid}/postings", params={"limit": 100, "offset": offset}).json()
            content = d.get("content") or []
            for p in content:
                loc = p.get("location") or {}
                loc_txt = ", ".join(x for x in [loc.get("city"), loc.get("country")] if x)
                if must and not any(m in norm(loc_txt + " " + (loc.get("fullLocation") or "")) for m in must) \
                        and (loc.get("country") or "").lower() != "lu":
                    continue
                pid = str(p["id"])
                jobs[pid] = RawJob(
                    source_id=self.source["id"], external_id=pid,
                    url=f"https://jobs.smartrecruiters.com/{cid}/{pid}",
                    title=p.get("name", ""), company=(p.get("company") or {}).get("name") or self.config.get("company", ""),
                    location=loc_txt, published_at=parse_date_any(p.get("releasedDate") or ""), published_is_estimate=False,
                    contract=((p.get("typeOfEmployment") or {}).get("label") or ""),
                )
            offset += len(content)
            if not content or offset >= int(d.get("totalFound") or 0) or offset >= 500:
                break
        return SourceResult(jobs=list(jobs.values()), complete=offset >= int(d.get("totalFound") or 0))

    def enrich(self, job: RawJob) -> RawJob:
        d = self.get(f"{API}/{self.config['company_id']}/postings/{job.external_id}").json()
        sec = ((d.get("jobAd") or {}).get("sections") or {})
        job.description = html_to_text("\n".join((sec.get(k) or {}).get("text", "") for k in
                                                 ("jobDescription", "qualifications", "additionalInformation")))
        job.url = d.get("postingUrl") or job.url
        job.languages = extract_languages(job.description)
        return job
