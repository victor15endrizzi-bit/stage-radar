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
        row_must = [norm(x) for x in self.config.get("row_must_contain", [])]
        base = self.config.get("base_url", "")
        jobs: dict[str, RawJob] = {}
        for url in self.config["list_urls"]:
            html = self.get(url).text
            for j in parse_links(html, base or url, pattern, self.source["id"], self.config.get("company", ""), must, row_must):
                jobs.setdefault(j.external_id, j)
        if not jobs and not must and not row_must:
            raise RuntimeError("Aucun lien d'offre trouvé : le motif ou la page a peut-être changé.")
        return SourceResult(jobs=list(jobs.values()), complete=True)

    def enrich(self, job: RawJob) -> RawJob:
        if self.config.get("fetch_details", True):
            enrich_from_page(self, job)
        req = [norm(x) for x in self.config.get("require_location", [])]
        if req and not any(r in norm(job.location + " " + job.description[:3000] + " " + job.title) for r in req):
            job.extra["drop"] = "hors zone"
        return job


def row_of(a):
    """Ligne de liste (li / tr / article) qui contient le lien : souvent elle porte le lieu et le contrat."""
    node = a
    for _ in range(6):
        node = node.parent
        if node is None:
            return None
        if node.name in ("li", "tr", "article"):
            return node
    return None


def parse_links(html: str, base: str, pattern: re.Pattern, source_id: str, company: str,
                must: list[str], row_must: list[str] | None = None) -> list[RawJob]:
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
        row = row_of(a)
        row_parts = [s for s in row.stripped_strings] if row else []
        if row_must and not any(m in norm(" ".join(row_parts)) for m in row_must):
            continue
        key = re.sub(r"[?&](LCID|lang)=[^&]*", "", href)
        if key in seen:
            continue
        seen.add(key)
        loc = ""
        m = re.search(r"\(([^()]*(?:LUX|Luxembourg)[^()]*)\)\s*$", text)
        if m:
            loc, text = m.group(1), text[: m.start()].strip()
        contract = ""
        if row_must and row_parts:
            hits = [p for p in row_parts if any(x in norm(p) for x in row_must)]
            loc = loc or (hits[0] if hits else "")
            contract = next((p for p in row_parts if norm(p) in ("stage", "internship", "cdi", "cdd", "v.i.e", "vie",
                                                                    "alternance", "permanent", "temporary")), "")
        out.append(RawJob(source_id=source_id, external_id=key, url=href, title=text, company=company,
                          location=loc, contract=contract))
    return out
