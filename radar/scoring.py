"""Analyse d'une offre et score de pertinence sur 100.

Le score est la somme de critères explicites (tous réglables dans la page Paramètres) :
  base 10
+ métier : meilleure famille (AM front 45 / AM middle 40 / marchés 40 / finance adjacente 25)
           plein si le mot-clé est dans le TITRE, sinon jusqu'à 60 % selon la DESCRIPTION
           (c'est ce qui rattrape les intitulés inhabituels)
+ 25 % d'une 2e famille (max 10)
+ stage / trainee / V.I.E.  (+10)       + entreprise cible (+8)
+ niveau Master demandé (+5) / BTS-Bachelor seulement (−10)
+ publiée < 48 h (+5)                   + tes compétences citées (CFA, VBA, Python… +2 chacune, max 6)
− métier hors finance dans le titre (−35)
± tes retours 👍/👎 sur l'entreprise (±6 par retour, max ±18)
Chaque point est expliqué dans `details`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .models import RawJob
from .textutil import (clean_title, company_norm, extract_duration, extract_languages, extract_sections,
                       extract_start, find_keywords, norm)


@dataclass
class Analysis:
    title: str
    company: str
    company_norm: str
    is_internship: bool
    duration: Optional[str]
    start_date: Optional[str]
    languages: list[str]
    missions: list[str]
    requirements: list[str]
    domain: str                  # famille principale (clé)
    domain_label: str
    score: int
    priority: str                # prioritaire | interessante | faible | hors_stage
    details: list[dict] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def analyze(job: RawJob, settings: dict, company_bias: Optional[dict] = None,
            now: Optional[datetime] = None) -> Analysis:
    now = now or datetime.now(timezone.utc)
    mods = settings.get("modifiers", {})
    fams = settings.get("families", {})
    title = clean_title(job.title)
    t_title = " " + norm(title) + " "
    t_desc = " " + norm(job.description) + " "
    t_all = t_title + " " + norm(job.contract) + " " + t_desc
    details: list[dict] = []

    def add(points: int, label: str, why: str = ""):
        if points:
            details.append({"points": int(round(points)), "label": label, "why": why})

    add(10, "Base")

    # --- métier
    fam_scores = []
    for key, fam in fams.items():
        w = float(fam.get("weight", 0))
        kws = fam.get("keywords", [])
        ht = find_keywords(t_title, kws)
        hd = [k for k in find_keywords(t_desc, kws) if k not in ht]
        if ht:
            s = w
        else:
            s = w * min(0.6, 0.2 * len(set(hd)))
        fam_scores.append((s, key, fam.get("label", key), ht, hd))
    fam_scores.sort(key=lambda x: -x[0])
    best = fam_scores[0] if fam_scores and fam_scores[0][0] > 0 else None
    if best:
        s, key, label, ht, hd = best
        where = f"titre : {', '.join(ht[:3])}" if ht else f"missions : {', '.join(hd[:4])}"
        add(s, f"Métier — {label}", where)
        second = next((f for f in fam_scores[1:] if f[0] > 0), None)
        if second:
            add(min(10, second[0] * 0.25), f"Bonus — {second[2]}", ", ".join((second[3] or second[4])[:3]))
        domain, domain_label = key, label
    else:
        domain, domain_label = "autre", "Autre / non identifié"

    # --- stage ?
    is_intern = job.is_internship_hint if job.is_internship_hint is not None else bool(
        find_keywords(t_title + " " + norm(job.contract), settings.get("internship_keywords", []))
        or "convention de stage" in t_desc or "internship agreement" in t_desc or "stage conventionne" in t_all
    )
    if is_intern:
        add(mods.get("internship_bonus", 10), "C'est un stage / trainee / V.I.E.")

    # --- entreprise cible
    cn = company_norm(job.company)
    tc = [norm(c) for c in settings.get("target_companies", [])]
    if any(c and (c in norm(job.company) or c == cn) for c in tc):
        add(mods.get("target_company_bonus", 8), "Entreprise de ta liste cible", job.company)

    # --- niveau
    if find_keywords(t_desc, ["master", "bac+5", "bac +5", "msc", "postgraduate", "graduate student", "m2"]):
        add(mods.get("master_level_bonus", 5), "Niveau Master demandé (= ton niveau)")
    elif find_keywords(t_desc, ["bts", "bachelor", "licence", "bac+2", "bac+3", "dut", "but "]):
        add(mods.get("low_level_malus", -10), "Niveau Bachelor/BTS visé", "souvent moins qualifiant pour un M2")

    # --- récence
    if job.published_at and (now - job.published_at).total_seconds() < 48 * 3600:
        add(mods.get("recent_bonus", 5), "Publiée il y a moins de 48 h")

    # --- compétences
    skills = find_keywords(t_all, settings.get("skills", []))
    if skills:
        add(min(mods.get("skills_bonus_max", 6), len(skills) * mods.get("skills_bonus_per_hit", 2)),
            "Compétences que tu as", ", ".join(skills[:4]))

    # --- hors cible
    off = find_keywords(t_title, settings.get("off_target_keywords", []))
    core_in_title = best is not None and bool(best[3]) and best[1] != "finance_adjacent"
    if off and not core_in_title:
        add(mods.get("off_target_malus", -35), "Métier hors finance", ", ".join(off[:3]))

    # --- tes retours
    if company_bias and cn in company_bias and company_bias[cn]:
        b = company_bias[cn]
        add(b, "Tes retours sur cette entreprise", "👍" if b > 0 else "👎")

    score = max(0, min(100, sum(d["points"] for d in details)))
    th = settings.get("thresholds", {})
    if not is_intern:
        priority = "hors_stage"
    elif score >= th.get("priority", 60):
        priority = "prioritaire"
    elif score >= th.get("interesting", 35):
        priority = "interessante"
    else:
        priority = "faible"

    missions, reqs = extract_sections(job.description)
    reasons = [d["label"] + (f" ({d['why']})" if d["why"] else "") for d in details
               if d["points"] > 0 and d["label"] != "Base"][:4]
    return Analysis(
        title=title, company=job.company.strip(), company_norm=cn, is_internship=is_intern,
        duration=extract_duration(job.title + "\n" + job.description),
        start_date=extract_start(job.title + "\n" + job.description),
        languages=job.languages or extract_languages(job.description),
        missions=missions, requirements=reqs, domain=domain, domain_label=domain_label,
        score=int(score), priority=priority, details=details, reasons=reasons,
    )
