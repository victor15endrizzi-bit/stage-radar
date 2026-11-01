"""Plan de site XML (sitemap.xml) d'un site carrière — ex. Deloitte, KPMG, Nordea (SAP SuccessFactors RMK).

Méthode : le plan de site public liste TOUTES les offres en ligne ; on garde celles dont l'URL
correspond au motif (et, si demandé, au lieu), puis on lit la fiche de chaque NOUVELLE offre.
Config : {sitemap_url, link_pattern, url_must_contain (optionnel), company, default_location}
"""
from __future__ import annotations

import html
import re
from urllib.parse import unquote

from ..models import RawJob, SourceResult
from ..textutil import norm, parse_date_any
from .base import Connector, enrich_from_page


def title_from_url(url: str) -> str:
    m = re.search(r"/job/([^/]+)/\d+/?$", url)
    slug = unquote(m.group(1)) if m else url.rstrip("/").rsplit("/", 1)[-1]
    return re.sub(r"\s+", " ", slug.replace("-", " ")).strip()


class SitemapConnector(Connector):
    kind = "sitemap"

    def fetch(self) -> SourceResult:
        c = self.config
        xml = self.get(c["sitemap_url"]).text
        pat = re.compile(c.get("link_pattern", r"/job/"))
        must = [norm(x) for x in c.get("url_must_contain", [])]
        jobs = []
        for block in re.findall(r"<url>(.*?)</url>", xml, re.S) or re.findall(r"(<loc>.*?</loc>)", xml, re.S):
            loc = re.search(r"<loc>(.*?)</loc>", block, re.S)
            if not loc:
                continue
            url = html.unescape(loc.group(1).strip())
            if not pat.search(url):
                continue
            if must and not any(m in norm(unquote(url)) for m in must):
                continue
            lastmod = re.search(r"<lastmod>(.*?)</lastmod>", block)
            jobs.append(RawJob(source_id=self.source["id"], external_id=url.rstrip("/"), url=url,
                               title=title_from_url(url), company=c.get("company", ""),
                               location=c.get("default_location", ""),
                               published_at=parse_date_any(lastmod.group(1)) if lastmod else None,
                               published_is_estimate=True))
        if not jobs and not must:
            raise RuntimeError("Aucune offre dans le plan de site : il a peut-être changé.")
        return SourceResult(jobs=jobs, complete=True)

    def enrich(self, job: RawJob) -> RawJob:
        slug_title = job.title
        job.title = ""
        enrich_from_page(self, job)
        job.title = job.title or slug_title
        if not job.location:
            job.location = self.config.get("default_location", "")
        return job
