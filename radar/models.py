"""Structures de données partagées par les connecteurs et le pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class RawJob:
    """Offre telle que renvoyée par un connecteur (avant dédoublonnage / scoring)."""

    source_id: str
    external_id: str              # identifiant stable dans la source (URL canonique ou id)
    url: str                      # lien direct vers l'annonce originale
    title: str
    company: str = ""
    location: str = ""
    description: str = ""
    contract: str = ""            # "Stage", "CDI", "Internship"...
    published_at: Optional[datetime] = None   # date de publication si fiable
    published_is_estimate: bool = False       # True si déduite d'un "il y a 2 h"
    languages: list[str] = field(default_factory=list)
    is_internship_hint: Optional[bool] = None  # la source dit explicitement stage / pas stage
    extra: dict = field(default_factory=dict)


@dataclass
class SourceResult:
    """Résultat d'un passage sur une source."""

    jobs: list[RawJob]
    complete: bool = True   # True si toute la liste a été lue (nécessaire pour détecter les offres expirées)
    warnings: list[str] = field(default_factory=list)
