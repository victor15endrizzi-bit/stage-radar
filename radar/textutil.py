"""Outils texte : normalisation, extraction d'informations dans les annonces."""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Optional

from bs4 import BeautifulSoup


def strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def norm(s: str) -> str:
    """Minuscules, sans accents, espaces compactés."""
    s = strip_accents((s or "").lower())
    s = s.replace("’", "'")
    return re.sub(r"\s+", " ", s).strip()


_kw_cache: dict[str, re.Pattern] = {}


def kw_pattern(kw: str) -> re.Pattern:
    """Mot-clé -> regex. Frontière à gauche ; les mots courts (<=3 lettres) doivent être des mots entiers.
    Les mots-clés plus longs matchent en préfixe ("manag" -> management, manager)."""
    if kw not in _kw_cache:
        k = norm(kw)
        esc = re.escape(k)
        if len(k.replace(".", "")) <= 3 or kw.endswith(" "):
            pat = rf"(?<![a-z0-9]){esc}(?![a-z0-9])"
        else:
            pat = rf"(?<![a-z0-9]){esc}"
        _kw_cache[kw] = re.compile(pat)
    return _kw_cache[kw]


def find_keywords(text_norm: str, keywords: list[str]) -> list[str]:
    return [k.strip() for k in keywords if k.strip() and kw_pattern(k).search(text_norm)]


GENDER_TAGS = re.compile(
    r"\((?:[mfhwdxn]\s*[/|,]\s*)+[mfhwdxn]\)|\b(?:[mfhwdxn]\s*/\s*){1,3}[mfhwdxn]\b|\(\s*f/m/d\s*\)", re.I
)


def clean_title(t: str) -> str:
    t = GENDER_TAGS.sub(" ", t or "")
    t = re.sub(r"\s*[-–|]\s*$", "", t)
    return re.sub(r"\s+", " ", t).strip()


LEGAL_SUFFIX = re.compile(r"\b(s ?a ?r ?l|sarl|s ?a|scs|sca|se|ag|gmbh|plc|ltd|llc|inc|branch|succursale|group|groupe)\b")

# Même entreprise, plusieurs noms selon les sites
COMPANY_ALIASES = {
    "banque internationale a luxembourg": "bil",
    "spuerkeess": "spuerkeess", "bcee": "spuerkeess", "banque et caisse d'epargne": "spuerkeess",
    "bgl bnp": "bgl bnp paribas",
    "quintet": "quintet",
    "candriam": "candriam",
    "lombard odier": "lombard odier",
    "pgim": "pgim", "prudential": "pgim",
    "wellington": "wellington",
    "pictet": "pictet",
}


def company_norm(name: str, aliases: Optional[dict[str, str]] = None) -> str:
    n = norm(name)
    for k, v in {**COMPANY_ALIASES, **(aliases or {})}.items():
        if norm(k) in n:
            return v
    n = re.sub(r"\(.*?\)", " ", n)
    n = re.sub(r"[^a-z0-9 ]", " ", n)
    n = LEGAL_SUFFIX.sub(" ", n)
    n = re.sub(r"\s+", " ", n).strip()
    # "xxx luxembourg" -> "xxx" seulement s'il reste un vrai nom
    stripped = re.sub(r"\s+(luxembourg|lux)$", "", n).strip()
    if len(stripped.replace(" ", "")) >= 3 and stripped not in ("banque de", "bank of", "banque"):
        n = stripped
    return n


def title_key(title: str) -> str:
    t = norm(clean_title(title))
    t = re.sub(r"\b(stage|stagiaire|internship|intern|trainee|praktikum|praktikant)\b", " ", t)
    t = re.sub(r"\b\d+\s*(mois|months?|monate)\b", " ", t)
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for tag in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "ul", "ol", "tr"]):
        tag.insert_after("\n")
    text = soup.get_text()
    lines = [re.sub(r"[ \t\xa0]+", " ", l).strip() for l in text.splitlines()]
    out, blank = [], 0
    for l in lines:
        if not l:
            blank += 1
            if blank <= 1:
                out.append("")
        else:
            blank = 0
            out.append(l)
    return "\n".join(out).strip()


# ----------------------------------------------------------------- extraction d'informations
MONTHS = {
    "janvier": 1, "january": 1, "januar": 1, "jan": 1,
    "fevrier": 2, "february": 2, "februar": 2, "feb": 2,
    "mars": 3, "march": 3, "marz": 3, "mar": 3,
    "avril": 4, "april": 4, "apr": 4,
    "mai": 5, "may": 5,
    "juin": 6, "june": 6, "juni": 6, "jun": 6,
    "juillet": 7, "july": 7, "juli": 7, "jul": 7,
    "aout": 8, "august": 8, "aug": 8,
    "septembre": 9, "september": 9, "sept": 9, "sep": 9,
    "octobre": 10, "october": 10, "oktober": 10, "oct": 10,
    "novembre": 11, "november": 11, "nov": 11,
    "decembre": 12, "december": 12, "dezember": 12, "dec": 12,
}
MONTH_NAMES_FR = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
                  "septembre", "octobre", "novembre", "décembre"]


def extract_duration(text: str) -> Optional[str]:
    t = norm(text)
    m = re.search(r"(\d{1,2})\s*(?:a|to|-|–|bis|ou|or)\s*(\d{1,2})\s*(mois|months?|monate)", t)
    if m:
        return f"{m.group(1)} à {m.group(2)} mois"
    m = re.search(r"(\d{1,2})[\s-]*(mois|months?|monate|month)\b", t)
    if m and 1 <= int(m.group(1)) <= 24:
        return f"{m.group(1)} mois"
    return None


def extract_start(text: str) -> Optional[str]:
    t = norm(text)
    # "à partir de janvier 2027", "starting January 2027", "from 01/01/2027", "start date: March 2027"
    m = re.search(
        r"(?:a partir d[eu]?|des|starting(?: in| on| from)?|start(?:ing)? date:?|from|commencing|debut:?|beginning|ab)\s+"
        r"(?:le |the |du |en |in )?(?:(\d{1,2})(?:st|nd|rd|th|er)?[ ./-]*)?([a-z]+|\d{1,2})[ ./-]+(20\d\d)", t)
    if m:
        mon = m.group(2)
        month = MONTHS.get(mon) if not mon.isdigit() else int(mon)
        if month and 1 <= month <= 12:
            return f"{MONTH_NAMES_FR[month]} {m.group(3)}"
    m = re.search(r"\b(" + "|".join(k for k in MONTHS if len(k) > 3) + r")\s+(20\d\d)\b", t)
    if m:
        return f"{MONTH_NAMES_FR[MONTHS[m.group(1)]]} {m.group(2)}"
    if re.search(r"des que possible|as soon as possible|asap|immediately|immediat", t):
        return "dès que possible"
    return None


LANG_PATTERNS = {
    "FR": r"\b(francais|french|franzosisch|langue francaise)\b",
    "EN": r"\b(anglais|english|englisch)\b",
    "DE": r"\b(allemand|german|deutsch)\b",
    "LU": r"\b(luxembourgeois|luxembourgish|luxemburgisch|letzebuergesch)\b",
    "NL": r"\b(neerlandais|dutch|niederlandisch)\b",
    "IT": r"\b(italien|italian|italienisch)\b",
}


def extract_languages(text: str) -> list[str]:
    t = norm(text)
    return [code for code, pat in LANG_PATTERNS.items() if re.search(pat, t)]


SECTION_MISSIONS = re.compile(
    r"(missions?|vos missions|your role|role|responsibilit|tasks|what you will do|your (?:main )?tasks|"
    r"your next challenge|key accountabilit|job description|description du poste|aufgaben|ihre aufgaben|"
    r"the position|activites)", re.I)
SECTION_PROFILE = re.compile(
    r"(profil|your profile|profile|requirements|qualifications|what we are looking for|your skills|skills|"
    r"we are looking for|competences|who you are|ihr profil|what you bring|experience required|prerequis)", re.I)


def extract_sections(text: str, max_items: int = 4) -> tuple[list[str], list[str]]:
    """Retourne (missions, prérequis) : quelques puces de chaque section."""
    missions, reqs, current = [], [], None
    for raw in (text or "").splitlines():
        line = raw.strip(" •-*\t")
        if not line:
            continue
        ln = norm(line)
        is_header = len(line) < 60 and (line.endswith(":") or not re.search(r"[.;]$", line)) and len(line.split()) <= 7
        if is_header and SECTION_PROFILE.search(ln):
            current = "req"
            continue
        if is_header and SECTION_MISSIONS.search(ln):
            current = "mis"
            continue
        if is_header and re.search(r"(we offer|nous vous offrons|benefits|avantages|about us|qui sommes|a propos|"
                                   r"wir bieten|informations|how to apply|postuler)", ln):
            current = None
            continue
        if current and 12 <= len(line) <= 300:
            (missions if current == "mis" else reqs).append(line)
    return missions[:max_items], reqs[:max_items]


def parse_relative_fr(s: str, now: Optional[datetime] = None) -> Optional[datetime]:
    """'il y a 2 h' / 'il y a 3 j' / 'il y a 5 min' / '2 days ago' -> datetime UTC approximative."""
    now = now or datetime.now(timezone.utc)
    t = norm(s)
    m = re.search(r"il y a\s+(\d+)\s*(min|h|j|sem|mois)", t) or re.search(r"(\d+)\s*(minutes?|hours?|days?|weeks?)\s+ago", t)
    if not m:
        if "aujourd" in t or "today" in t:
            return now
        if "hier" in t or "yesterday" in t:
            return now - timedelta(days=1)
        return None
    n, unit = int(m.group(1)), m.group(2)
    if unit.startswith("min"):
        d = timedelta(minutes=n)
    elif unit.startswith("h"):
        d = timedelta(hours=n)
    elif unit.startswith(("j", "d")):
        d = timedelta(days=n)
    elif unit.startswith(("sem", "w")):
        d = timedelta(weeks=n)
    else:
        d = timedelta(days=30 * n)
    return now - d


def parse_date_any(s: str) -> Optional[datetime]:
    """'10/10/2026', '2026-10-10', '2026-10-10T08:00:00Z' -> datetime UTC."""
    if not s:
        return None
    s = s.strip()
    m = re.search(r"(\d{1,2})[/.](\d{1,2})[/.](20\d\d)", s)
    if m:
        try:
            return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)), tzinfo=timezone.utc)
        except ValueError:
            return None
    try:
        from dateutil import parser as dp
        d = dp.isoparse(s) if re.match(r"\d{4}-\d{2}-\d{2}", s) else dp.parse(s, dayfirst=True)
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, TypeError):
        return None
