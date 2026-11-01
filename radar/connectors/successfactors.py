"""SAP SuccessFactors — flux XML public du site carrière « classique » (ex. Pictet).

URL : https://{host}/career?company={company_id}&career_ns=job_listing_summary&resultType=XML
Config : {host, company_id, company, country: Luxembourg}
"""
from __future__ import annotations

import html
import re

from ..models import RawJob, SourceResult
from ..textutil import extract_languages, html_to_text, norm
from .base import Connector


def _field(block: str, tag: str) -> str:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
    if not m:
        return ""
    v = m.group(1)
    v = re.sub(r"^<!\[CDATA\[|\]\]>$", "", v.strip())
    return v


def _filter_value(block: str, label: str) -> str:
    for tag in ("filter1", "filter2", "filter3", "filter4", "mfield1", "mfield2"):
        f = _field(block, tag)
        if re.search(rf"<label>\s*{label}\s*</label>", f, re.I):
            return html.unescape(re.sub(r"<.*?>", " ", _field(f, "value"))).strip()
    return ""


class SuccessFactorsConnector(Connector):
    kind = "successfactors"

    def fetch(self) -> SourceResult:
        c = self.config
        x = self.get(f"https://{c['host']}/career",
                     params={"company": c["company_id"], "career_ns": "job_listing_summary", "resultType": "XML"}).text
        blocks = re.findall(r"<Job>(.*?)</Job>", x, re.S)
        if not blocks:
            raise RuntimeError("Flux SuccessFactors vide ou modifié.")
        want = norm(c.get("country", "Luxembourg"))
        jobs = []
        for b in blocks:
            country, city = _filter_value(b, "Country"), _filter_value(b, "City")
            if want and want not in norm(country + " " + city):
                continue
            rid = _field(b, "ReqId")
            desc = html_to_text(html.unescape(_field(b, "Job-Description")))
            jobs.append(RawJob(
                source_id=self.source["id"], external_id=rid,
                url=f"https://{c['host']}/career?company={c['company_id']}&career_ns=job_listing&career_job_req_id={rid}",
                title=html.unescape(_field(b, "JobTitle")), company=c.get("company", ""),
                location=", ".join(x for x in [city, country] if x), description=desc,
                languages=extract_languages(desc), extra={"area": _filter_value(b, "Activity area")}))
        return SourceResult(jobs=jobs, complete=True)

    def enrich(self, job: RawJob) -> RawJob:
        return job  # le flux contient déjà la description complète
