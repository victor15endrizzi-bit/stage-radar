"""Base commune des connecteurs : session HTTP polie, reprises, lecture des fiches d'offres."""
from __future__ import annotations

import json
import logging
import time
from typing import Optional

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..models import RawJob, SourceResult
from ..textutil import extract_languages, html_to_text, parse_date_any

log = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (compatible; StageRadar/1.0; recherche de stage personnelle, "
    "1 requête toutes les quelques secondes)"
)


def make_session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=2, status_forcelist=(429, 500, 502, 503, 504),
                  allowed_methods=frozenset(["GET", "POST"]), respect_retry_after_header=True)
    s.mount("https://", HTTPAdapter(max_retries=retry))
    s.mount("http://", HTTPAdapter(max_retries=retry))
    s.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "fr-FR,fr;q=0.9,en;q=0.8"})
    return s


class Connector:
    """Un connecteur lit une source et renvoie des RawJob.

    - fetch()  : liste des offres actuellement visibles (léger, appelé à chaque passage)
    - enrich() : complète une NOUVELLE offre (description, date...) ; appelé une seule fois par offre
    """

    kind = "base"
    delay_seconds = 1.5  # politesse entre deux requêtes vers le même site

    def __init__(self, source: dict, session: Optional[requests.Session] = None):
        self.source = source
        self.config = source.get("config") or {}
        self.session = session or make_session()
        self._last_request = 0.0

    def _wait(self):
        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay_seconds:
            time.sleep(self.delay_seconds - elapsed)
        self._last_request = time.monotonic()

    def get(self, url: str, **kw) -> requests.Response:
        self._wait()
        r = self.session.get(url, timeout=kw.pop("timeout", 30), **kw)
        r.raise_for_status()
        return r

    def post(self, url: str, **kw) -> requests.Response:
        self._wait()
        r = self.session.post(url, timeout=kw.pop("timeout", 30), **kw)
        r.raise_for_status()
        return r

    def fetch(self) -> SourceResult:  # pragma: no cover - abstrait
        raise NotImplementedError

    def enrich(self, job: RawJob) -> RawJob:
        return enrich_from_page(self, job)


# --------------------------------------------------------------------------- fiches génériques
def parse_jsonld_jobposting(soup: BeautifulSoup) -> Optional[dict]:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        items = data if isinstance(data, list) else [data]
        for it in list(items):
            if isinstance(it, dict) and "@graph" in it:
                items.extend(it["@graph"])
        for it in items:
            if isinstance(it, dict) and str(it.get("@type", "")).lower() == "jobposting":
                return it
    return None


def main_text(soup: BeautifulSoup) -> str:
    for sel in ["main", "article", "[class*=job-description]", "[class*=description]", "[id*=description]", "body"]:
        node = soup.select_one(sel)
        if node and len(node.get_text(strip=True)) > 200:
            for bad in node.select("nav, header, footer, script, style, noscript, form"):
                bad.decompose()
            return html_to_text(str(node))
    return html_to_text(str(soup))


def apply_jsonld(job: RawJob, jp: dict) -> None:
    if jp.get("title") and not job.title:
        job.title = jp["title"]
    org = jp.get("hiringOrganization")
    if isinstance(org, dict) and org.get("name") and not job.company:
        job.company = org["name"]
    if jp.get("datePosted"):
        d = parse_date_any(str(jp["datePosted"]))
        if d:
            job.published_at, job.published_is_estimate = d, False
    if jp.get("validThrough"):
        job.extra["valid_through"] = str(jp["validThrough"])
    if jp.get("employmentType"):
        et = jp["employmentType"]
        job.contract = job.contract or (", ".join(et) if isinstance(et, list) else str(et))
    loc = jp.get("jobLocation")
    if loc and not job.location:
        locs = loc if isinstance(loc, list) else [loc]
        parts = []
        for l in locs:
            addr = (l or {}).get("address") or {}
            if isinstance(addr, dict):
                parts.append(", ".join(x for x in [addr.get("addressLocality"), addr.get("addressCountry")] if isinstance(x, str) and x))
        job.location = "; ".join(p for p in parts if p)
    if jp.get("description"):
        job.description = html_to_text(str(jp["description"]))


def enrich_from_page(conn: Connector, job: RawJob) -> RawJob:
    """Lit la fiche de l'offre : JSON-LD JobPosting si présent, sinon texte principal de la page."""
    r = conn.get(job.url)
    soup = BeautifulSoup(r.text, "lxml")
    jp = parse_jsonld_jobposting(soup)
    if jp:
        apply_jsonld(job, jp)
    if not job.description or len(job.description) < 200:
        job.description = main_text(soup)
    if not job.title:
        h1 = soup.find("h1")
        job.title = h1.get_text(" ", strip=True) if h1 else job.title
    if not job.languages:
        job.languages = extract_languages(job.description)
    return job
