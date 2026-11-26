"""Préparation des textes envoyés au LLM.

- Discogs : la bio nettoyée de son balisage par dbt (dim_artists.discogs_profile_clean, macro
  clean_discogs_markup), pour que la citation soit vérifiée dans le même texte côté Python et dbt.
- Wikipédia : l'article est long ; on envoie en priorité les sections qui parlent du son et des
  influences, puis l'introduction, dans la limite de `max_chars`. Les autres sections (histoire
  détaillée, discographie, références…) ne sont pas envoyées.
"""
from __future__ import annotations

import re

from lineage.sources.wikipedia import split_sections

MAX_WIKIPEDIA_CHARS = 6000
STYLE_SECTION_RE = re.compile(
    r"style|influence|sound|artistry|genre|musical|music and|legacy|aesthetic|reception", re.IGNORECASE
)
SKIP_SECTION_RE = re.compile(
    r"discograph|video|tour|member|personnel|award|reference|external|see also|notes|further|bibliograph"
    r"|filmograph|lawsuit|legal|controvers",
    re.IGNORECASE,
)


def wikipedia_passages(extract: str, max_chars: int = MAX_WIKIPEDIA_CHARS) -> str:
    """Sections « style / influences » d'abord, puis l'introduction, sans dépasser max_chars.

    Chaque passage garde son titre « == Titre == » tel qu'il est dans l'article : le LLM sait d'où
    vient le texte, et toute citation reste une sous-chaîne de l'article complet."""
    sections = split_sections(extract)
    style = [s for s in sections[1:] if STYLE_SECTION_RE.search(s[0]) and not SKIP_SECTION_RE.search(s[0])]
    ordered = [*style, sections[0]]
    parts: list[str] = []
    used = 0
    for title, level, text in ordered:
        if not text:
            continue
        block = f"{'=' * level} {title} {'=' * level}\n{text}" if title else text
        room = max_chars - used
        if room <= 200:
            break
        if len(block) > room:
            block = block[:room].rsplit(". ", 1)[0] + "."  # coupe à la fin d'une phrase
        parts.append(block)
        used += len(block) + 2
    return "\n\n".join(parts)
