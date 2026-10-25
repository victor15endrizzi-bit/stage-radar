"""Moovijob.com — premier site d'emploi luxembourgeois.

Méthode : lecture des pages de liste publiques (autorisées par leur robots.txt),
puis de la fiche de chaque NOUVELLE offre (date de publication, langues, description).
Limite : pas d'API ; si Moovijob change sa mise en page, la source passe en erreur
et tu reçois une alerte « source en panne ».
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup

from ..models import RawJob, SourceResult
from ..textutil import extract_languages, parse_date_any, parse_relative_fr
from .base import Connector, apply_jsonld, main_text, parse_jsonld_jobposting

JOB_PATH = re.compile(r"^/(?:offres-emploi|job-offers|stellenangebote)/(?!jobs-lux)([^/]+)/([^/?#]+)/?$")
CONTRACT_WORDS = {"stage", "cdi", "cdd", "alternance / apprentissage", "interim", "intérim", "internship",
                  "indépendant / freelance / franchise", "job étudiant / saisonnier", "graduate program / v.i.e."}


class MoovijobConnector(Connector):
    kind = "moovijob"

    def page_url(self, n: int) -> str:
        base = self.config["list_url"].rstrip("/")
        return base if n == 1 else f"{base}/page-{n}"

    def fetch(self) -> SourceResult:
        jobs: dict[str, RawJob] = {}
        max_pages = int(self.config.get("max_pages", 10))
        complete = False
        for n in range(1, max_pages + 1):
            html = self.get(self.page_url(n)).text
            page_jobs, has_next = parse_listing(html, self.source["id"], self.config.get("assume_internship", False))
            new = 0
            for j in page_jobs:
                if j.external_id not in jobs:
                    jobs[j.external_id] = j
                    new += 1
            if not has_next or new == 0:
                complete = True
                break
        if not jobs:
            raise RuntimeError("Aucune offre trouvée sur la page Moovijob : la mise en page a peut-être changé.")
        return SourceResult(jobs=list(jobs.values()), complete=complete)

    def enrich(self, job: RawJob) -> RawJob:
        html = self.get(job.url).text
        parse_detail(html, job)
        return job


def parse_listing(html: str, source_id: str, assume_internship: bool = False,
                  now: datetime | None = None) -> tuple[list[RawJob], bool]:
    soup = BeautifulSoup(html, "lxml")
    out: list[RawJob] = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = urljoin("https://www.moovijob.com", a["href"])
        p = urlparse(href)
        if "moovijob.com" not in p.netloc:
            continue
        m = JOB_PATH.match(p.path)
        if not m:
            continue
        company_slug = m.group(1)
        if company_slug.startswith("moovijob-") or company_slug in ("entreprise", "offres-formation"):
            continue  # annonces « salon » génériques, pas de vraies offres
        canonical = f"https://www.moovijob.com/offres-emploi/{company_slug}/{m.group(2)}"
        if canonical in seen:
            continue
        strings = [s for s in a.stripped_strings if s and s.lower() not in ("voir l'offre", "view offer", "logo")]
        if len(strings) < 2:
            continue
        seen.add(canonical)
        title, company = strings[0], strings[1]
        rel = next((s for s in strings if re.match(r"(il y a|\d+\s+\w+\s+ago|aujourd|hier)", s, re.I)), None)
        contracts = [s for s in strings[2:] if s.lower() in CONTRACT_WORDS]
        loc_candidates = [s for s in strings[2:] if s not in contracts and s != rel and not re.search(r"€|k €", s)]
        location = loc_candidates[0] if loc_candidates else ""
        hint = True if assume_internship and any(c.lower() == "stage" for c in contracts) else None
        if assume_internship and not contracts:
            hint = True
        out.append(RawJob(
            source_id=source_id, external_id=canonical, url=canonical, title=title, company=company,
            location=location, contract=", ".join(contracts),
            published_at=parse_relative_fr(rel, now) if rel else None,
            published_is_estimate=True, is_internship_hint=hint,
        ))
    # pagination : lien "page-(n+1)" ou "Suivant"
    has_next = bool(soup.find("a", string=re.compile(r"Suivant|Next|Weiter", re.I)))
    return out, has_next


def parse_detail(html: str, job: RawJob) -> RawJob:
    soup = BeautifulSoup(html, "lxml")
    jp = parse_jsonld_jobposting(soup)
    if jp:
        apply_jsonld(job, jp)
    text = main_text(soup)
    m = re.search(r"(?:Publiée le|Published on|Veröffentlicht am)\s*:?\s*(\d{1,2}/\d{1,2}/20\d\d)", text)
    if m:
        d = parse_date_any(m.group(1))
        if d:
            # la liste donne l'heure approximative ; la fiche donne le jour exact
            if not job.published_at or job.published_at.date() != d.date():
                job.published_at = d
            job.published_is_estimate = False
    lm = re.search(r"(?:Langues parlées|Spoken languages|Gesprochene Sprachen)\s*\n?((?:\s*[A-Z]{2}\b[\s,]*)+)", text)
    if lm:
        job.languages = sorted(set(re.findall(r"\b[A-Z]{2}\b", lm.group(1))))
    # découpe la description : à partir du nom de l'entreprise / du titre jusqu'au bloc "Postuler"
    if not job.description or len(job.description) < 200:
        start = text.find(job.title) if job.title in text else 0
        job.description = text[start:start + 8000]
    if not job.languages:
        job.languages = extract_languages(job.description)
    if job.published_at is None:
        job.published_at = datetime.now(timezone.utc)
        job.published_is_estimate = True
    return job
