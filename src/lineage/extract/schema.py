"""Format de sortie imposé au LLM pour l'extraction d'influences, et règles de validation.

Le LLM ne doit RIEN affirmer sans citer, mot pour mot et le plus brièvement possible, le passage
du texte qui le justifie. Après la réponse, `filter_claims` écarte :
- les citations absentes du texte source (`validate_against_source`) ou trop longues ;
- les « artistes » qui sont en fait des genres, courants, lieux ou labels ;
- les collaborations avec un membre ou un alias déjà connu par MusicBrainz (pas une information).
"""
from __future__ import annotations

import re
import unicodedata
from collections import Counter
from typing import Literal

from pydantic import BaseModel, Field

MAX_QUOTE_CHARS = 200


class InfluenceClaim(BaseModel):
    other_artist: str = Field(description="Nom de l'autre artiste ou groupe, tel qu'écrit dans le texte")
    relation: Literal["influenced_by", "influenced", "compared_to", "collaborated_with", "sampled"]
    evidence_quote: str = Field(
        description="Le plus court passage copié mot pour mot du texte qui justifie la relation "
                    "(quelques mots, jamais plus d'une phrase)"
    )
    confidence: float = Field(ge=0, le=1)


class ExtractionResult(BaseModel):
    claims: list[InfluenceClaim] = Field(default_factory=list)


SYSTEM_PROMPT = """Tu extrais des relations entre artistes musicaux à partir d'un texte (bio ou article).
Règles :
- N'utilise QUE le texte fourni, jamais tes connaissances.
- other_artist est TOUJOURS un artiste ou un groupe précis, nommé dans le texte. Jamais un genre
  (soul, techno, witch house…), un courant, une scène, une tradition, un lieu, un label ou une
  œuvre : ceux-là, ignore-les.
- N'extrais pas les membres, anciens membres ou alias de l'artiste : ce ne sont pas des influences.
- evidence_quote : copie mot pour mot le passage le PLUS COURT qui justifie la relation, quelques
  mots si possible, jamais plus d'une phrase.
- « influencé par X », « inspiré par X », « cite X comme influence », « dans la lignée de X » → influenced_by.
- « a influencé X », « X cite l'artiste comme influence » → influenced.
- « comparé à X », « rappelle X », « proche de X » → compared_to (ce n'est PAS une influence revendiquée).
- « a travaillé / enregistré / tourné avec X » → collaborated_with ; « sample X » → sampled.
- confidence : 0.9 si le texte l'affirme explicitement, 0.6 si c'est une comparaison ou une
  formulation indirecte, 0.3 si c'est ambigu.
- Si le texte ne contient aucune relation de ce type, renvoie une liste vide.
Réponds uniquement en JSON conforme au schéma."""

# Mots qui trahissent un genre, un courant, un lieu ou un label pris pour un artiste.
NOT_AN_ARTIST_RE = re.compile(
    r"\b(music|musique|scene|scène|genre|movement|mouvement|style|sound|tradition|folk|culture|"
    r"mythology|records?|recordings|label|city|county|state|province|region)\b",
    re.IGNORECASE,
)
COMMON_GENRES = {
    "soul", "jazz", "blues", "rock", "punk", "post punk", "hip hop", "rap", "trap", "techno", "house",
    "witch house", "drone", "shoegaze", "dream pop", "darkwave", "coldwave", "goth", "gothic rock",
    "industrial", "noise", "ambient", "electronic", "electronica", "edm", "pop", "synth pop", "synthpop",
    "new wave", "metal", "black metal", "grunge", "disco", "funk", "r and b", "rnb", "chopped and screwed",
    "dubstep", "trip hop", "post rock", "krautrock", "emo", "hardcore", "lo fi", "chillwave", "vaporwave",
    "witch", "gospel", "country", "reggae", "dub", "classical", "opera", "indie", "indie rock", "garage",
}
# Relations MusicBrainz qui disent qui EST l'artiste (membres, alias), pas qui l'inspire.
MB_IDENTITY_EDGE_TYPES = {
    "member of band", "is person", "subgroup", "founder", "supporting musician",
    "instrumental supporting musician", "vocal supporting musician", "collaboration",
}


def normalize_name(name: str) -> str:
    """Même normalisation que la macro dbt normalize_artist_name."""
    s = unicodedata.normalize("NFKD", name.casefold())
    s = "".join(c for c in s if not unicodedata.combining(c)).replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    return re.sub(r"^the ", "", s)


def _norm_text(text: str) -> str:
    return " ".join(text.split()).casefold()


def validate_against_source(result: ExtractionResult, source_text: str) -> ExtractionResult:
    """Écarte toute affirmation dont la citation n'apparaît pas dans le texte source."""
    normalized = _norm_text(source_text)
    kept = [c for c in result.claims if c.evidence_quote.strip() and _norm_text(c.evidence_quote) in normalized]
    return ExtractionResult(claims=kept)


def is_not_an_artist(name: str, known_genres: set[str] = frozenset()) -> bool:
    key = normalize_name(name)
    return not key or key in COMMON_GENRES or key in known_genres or bool(NOT_AN_ARTIST_RE.search(name))


def filter_claims(
    result: ExtractionResult,
    source_text: str,
    artist_name: str = "",
    known_genres: set[str] = frozenset(),
    known_identity_names: set[str] = frozenset(),
) -> tuple[ExtractionResult, Counter]:
    """Applique toutes les règles. Renvoie (affirmations gardées, nombre de rejets par motif).

    known_genres : genres et tags MusicBrainz de la base, normalisés.
    known_identity_names : noms normalisés des membres / alias / collaborations déjà connus par
    MusicBrainz pour cet artiste."""
    rejected: Counter = Counter()
    in_source = {id(c) for c in validate_against_source(result, source_text).claims}
    kept = []
    seen = set()
    for c in result.claims:
        key = normalize_name(c.other_artist)
        if id(c) not in in_source:
            rejected["citation absente du texte"] += 1
        elif len(c.evidence_quote.strip()) > MAX_QUOTE_CHARS:
            rejected["citation trop longue"] += 1
        elif is_not_an_artist(c.other_artist, known_genres):
            rejected["genre, lieu ou label"] += 1
        elif artist_name and key == normalize_name(artist_name):
            rejected["l'artiste lui-même"] += 1
        elif c.relation == "collaborated_with" and key in known_identity_names:
            rejected["membre ou alias déjà connu"] += 1
        elif (key, c.relation) in seen:
            rejected["doublon"] += 1
        else:
            seen.add((key, c.relation))
            kept.append(c)
    return ExtractionResult(claims=kept), rejected
