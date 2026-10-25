"""Page carrière HTML générique.

Méthode : on lit la page qui liste les offres et on garde les liens qui correspondent
à un motif (regex) propre au site ; la fiche est lue ensuite (JSON-LD si disponible).
Ajouter une entreprise = ajouter une source avec son URL de liste et son motif de lien.
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import RawJob, SourceResult
from ..textutil import norm
from .base import Connector, enrich_from_page


class LinksConnector(Connector):
    kind = "links"

    def fetch(self) -> SourceResult:
        pattern = re.compile(self.config["link_pattern"])
        must = [norm(x) for x in self.config.get("anchor_must_contain", [])]
        base = self.config.get("base_url", "")
        jobs: dict[str, RawJob] = {}
        for url in self.config["list_urls"]:
            html = self.get(url).text
            for j in parse_links(html, base or url, pattern, self.source["id"], self.config.get("company", ""), must):
                jobs.setdefault(j.external_id, j)
        if not jobs and not must:
            raise RuntimeError("Aucun lien d'offre trouvé : le motif ou la page a peut-être changé.")
        return SourceResult(jobs=list(jobs.values()), complete=True)

    def enrich(self, job: RawJob) -> RawJob:
        if self.config.get("fetch_details", True):
            enrich_from_page(self, job)
        req = [norm(x) for x in self.config.get("require_location", [])]
        if req and not any(r in norm(job.location + " " + job.description[:3000] + " " + job.title) for r in req):
            job.extra["drop"] = "hors zone"
        return job


def parse_links(html: str, base: str, pattern: re.Pattern, source_id: str, company: str,
                must: list[str]) -> list[RawJob]:
    soup = BeautifulSoup(html, "lxml")
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"]).split("#")[0]
        if not pattern.search(href):
            continue
        text = a.get_text(" ", strip=True) or a.get("title", "")
        if not text or len(text) < 4:
            continue
        if must and not any(m in norm(text) for m in must):
            continue
        key = re.sub(r"[?&](LCID|lang)=[^&]*", "", href)
        if key in seen:
            continue
        seen.add(key)
        loc = ""
        m = re.search(r"\(([^()]*(?:LUX|Luxembourg)[^()]*)\)\s*$", text)
        if m:
            loc, text = m.group(1), text[: m.start()].strip()
        out.append(RawJob(source_id=source_id, external_id=key, url=href, title=text, company=company, location=loc))
    return out
