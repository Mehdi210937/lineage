"""Format de sortie imposé au LLM pour l'extraction d'influences (phase 3).

Le LLM ne doit RIEN affirmer sans citer la phrase exacte de la bio qui le justifie.
Une citation absente du texte source = affirmation rejetée (voir `validate_against_source`).
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class InfluenceClaim(BaseModel):
    other_artist: str = Field(description="Nom de l'autre artiste tel qu'écrit dans le texte")
    relation: Literal["influenced_by", "influenced", "compared_to", "collaborated_with", "sampled"]
    evidence_quote: str = Field(description="Phrase copiée mot pour mot depuis le texte source")
    confidence: float = Field(ge=0, le=1)


class ExtractionResult(BaseModel):
    claims: list[InfluenceClaim] = Field(default_factory=list)


SYSTEM_PROMPT = """Tu extrais des relations entre artistes musicaux à partir d'une biographie.
Règles :
- N'utilise QUE le texte fourni, jamais tes connaissances.
- Pour chaque relation, copie mot pour mot la phrase qui la justifie dans evidence_quote.
- « influencé par X », « inspiré par X », « dans la lignée de X » → influenced_by.
- « comparé à X », « rappelle X » → compared_to (ce n'est PAS une influence revendiquée).
- Si le texte ne contient aucune relation, renvoie une liste vide.
Réponds uniquement en JSON conforme au schéma."""


def validate_against_source(result: ExtractionResult, source_text: str) -> ExtractionResult:
    """Écarte toute affirmation dont la citation n'apparaît pas dans le texte source."""
    normalized = " ".join(source_text.split()).casefold()
    kept = [c for c in result.claims if " ".join(c.evidence_quote.split()).casefold() in normalized]
    return ExtractionResult(claims=kept)
