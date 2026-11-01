"""Sites carrière Cornerstone OnDemand (*.csod.com), ex. Spuerkeess.

Méthode : exactement comme la page carrière publique — on ouvre la page, on récupère le
jeton « visiteur » qu'elle contient, puis on appelle la recherche d'offres qu'elle utilise.
Config : {tenant, career_site (1), culture_id, culture_name, company}
"""
from __future__ import annotations

import re

from ..models import RawJob, SourceResult
from ..textutil import extract_languages, html_to_text, parse_date_any
from .base import Connector


class CornerstoneConnector(Connector):
    kind = "cornerstone"
    delay_seconds = 1.0

    def fetch(self) -> SourceResult:
        c = self.config
        tenant, site = c["tenant"], int(c.get("career_site", 1))
        page = self.get(f"https://{tenant}.csod.com/ux/ats/careersite/{site}/home?c={tenant}").text
        tok = re.search(r'"token":"([^"]+)"', page)
        api = re.search(r'"cloud":"(https://[^"]+)"', page)
        if not tok or not api:
            raise RuntimeError("Jeton Cornerstone introuvable : la page carrière a changé.")
        url = api.group(1).rstrip("/") + "/rec-job-search/external/jobs"
        hdr = {"Authorization": f"Bearer {tok.group(1)}", "Content-Type": "application/json",
               "Accept-Language": None}  # leur API refuse l'en-tête de langue multiple (400)
        jobs, n = {}, 1
        while True:
            body = {"careerSiteId": site, "careerSitePageId": site, "pageNumber": n, "pageSize": 25,
                    "cultureId": int(c.get("culture_id", 3)), "searchText": "", "cultureName": c.get("culture_name", "fr-FR"),
                    "states": [], "countryCodes": [], "cities": [], "placeID": "", "radius": None,
                    "postingsWithinDays": None, "customFieldCheckboxKeys": [], "customFieldDropdowns": [],
                    "customFieldRadios": []}
            d = (self.post(url, json=body, headers=hdr).json() or {}).get("data") or {}
            reqs = d.get("requisitions") or []
            countries = [x.upper() for x in c.get("countries", [])]
            for q in reqs:
                rid = str(q["requisitionId"])
                if countries and not any((l.get("country") or "").upper() in countries for l in q.get("locations") or []):
                    continue
                locs = ", ".join(f"{l.get('city', '').title()} ({l.get('country', '')})" for l in q.get("locations") or [])
                desc = html_to_text(q.get("externalDescription") or "")
                jobs[rid] = RawJob(
                    source_id=self.source["id"], external_id=rid,
                    url=f"https://{tenant}.csod.com/ux/ats/careersite/{site}/home/requisition/{rid}?c={tenant}",
                    title=html_to_text(q.get("displayJobTitle") or ""), company=c.get("company", tenant),
                    location=locs, description=desc, published_at=parse_date_any(q.get("postingEffectiveDate") or ""),
                    published_is_estimate=False, languages=extract_languages(desc),
                    extra={"valid_through": q.get("postingExpirationDate")})
            if not reqs or n * 25 >= int(d.get("totalCount") or 0) or n >= 10:
                break
            n += 1
        return SourceResult(jobs=list(jobs.values()), complete=n * 25 >= int(d.get("totalCount") or 0))

    def enrich(self, job: RawJob) -> RawJob:
        return job  # la recherche renvoie déjà la description
