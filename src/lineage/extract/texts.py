"""Préparation des textes envoyés au LLM.

- Discogs : la bio nettoyée de son balisage par dbt (dim_artists.discogs_profile_clean, macro
  clean_discogs_markup), un seul morceau.
- Wikipédia : tout l'article est lu, dans cet ordre : les sections sur le style et les influences,
  puis l'introduction, puis le reste (historique…), sans les sections sans intérêt (discographie,
  références…). Le tout est découpé en morceaux d'au plus CHUNK_CHARS caractères, chacun envoyé au
  LLM séparément.

Chaque morceau est ensuite découpé en phrases numérotées : le LLM renvoie le numéro de la phrase
qui justifie une relation au lieu de la recopier, et la citation stockée est la phrase originale.
Plus aucune reformulation n'est possible.
"""
from __future__ import annotations

import re

from lineage.sources.wikipedia import split_sections

CHUNK_CHARS = 3000
MAX_CHUNKS = 6
STYLE_SECTION_RE = re.compile(
    r"style|influence|sound(?!track)|artistry|genre|musical|music and|legacy|aesthetic", re.IGNORECASE
)
SKIP_SECTION_RE = re.compile(
    r"discograph|video|tour|member|personnel|award|reference|external|see also|notes|further|bibliograph"
    r"|filmograph|lawsuit|legal|controvers|soundtrack",
    re.IGNORECASE,
)
HEADING_LINE_RE = re.compile(r"^={2,6}.*={2,6}$")
# Fin de phrase : . ! ? (éventuellement suivis d'un guillemet ou d'une parenthèse), puis un blanc et
# une majuscule, un chiffre ou un guillemet ouvrant. Les initiales (« J. Mascis ») ne coupent pas.
SENTENCE_END_RE = re.compile(
    r"(?:(?<=[.!?])|(?<=[.!?][\"'”’)]))"   # après la ponctuation (et un éventuel guillemet fermant)
    r"(?<!\b[A-Z]\.)"                    # mais pas après une initiale (« J. Mascis »)
    r"\s+(?=[A-Z0-9\"'“‘(])"               # on ne coupe que le blanc : chaque phrase reste exacte
)


def wikipedia_blocks(extract: str) -> list[str]:
    """Sections dans l'ordre de lecture : style / influences, introduction, puis le reste."""
    sections = split_sections(extract)
    style, rest = [], []
    for title, level, text in sections[1:]:
        if not text or SKIP_SECTION_RE.search(title):
            continue
        block = f"{'=' * level} {title} {'=' * level}\n{text}"
        (style if STYLE_SECTION_RE.search(title) else rest).append(block)
    intro = [sections[0][2]] if sections[0][2] else []
    return [*style, *intro, *rest]


def chunk_blocks(blocks: list[str], max_chars: int = CHUNK_CHARS, max_chunks: int = MAX_CHUNKS) -> list[str]:
    """Regroupe les blocs en morceaux d'au plus max_chars, en coupant les blocs trop longs entre
    paragraphes puis entre phrases."""
    pieces: list[str] = []
    for block in blocks:
        if len(block) <= max_chars:
            pieces.append(block)
            continue
        for para in block.split("\n"):
            if len(para) <= max_chars:
                pieces.append(para)
            else:
                pieces.extend(split_sentences(para))
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        if current and len(current) + 2 + len(piece) > max_chars:
            chunks.append(current)
            current = piece
        else:
            current = f"{current}\n\n{piece}" if current else piece
    if current:
        chunks.append(current)
    return chunks[:max_chunks]


def text_chunks(kind: str, body: str) -> list[str]:
    if kind == "wikipedia":
        return chunk_blocks(wikipedia_blocks(body))
    return [body]


def wikipedia_passages(extract: str, max_chars: int = 6000) -> str:
    """Extrait court pour le coach : sections style / influences puis introduction, coupé à max_chars."""
    out = "\n\n".join(chunk_blocks(wikipedia_blocks(extract), max_chars=max_chars, max_chunks=1))
    if len(out) > max_chars:
        out = out[:max_chars].rsplit(". ", 1)[0] + "."
    return out


def split_sentences(text: str) -> list[str]:
    """Phrases du texte, chacune sous-chaîne exacte du texte (rien n'est réécrit). Les lignes de titre
    « == Titre == » ne sont pas des phrases."""
    sentences = []
    for line in text.split("\n"):
        line = line.strip()
        if not line or HEADING_LINE_RE.match(line):
            continue
        sentences.extend(s.strip() for s in SENTENCE_END_RE.split(line) if s.strip())
    return sentences


def numbered(text: str) -> tuple[str, list[str]]:
    """(texte à envoyer au LLM, phrases). Les titres restent pour le contexte, sans numéro."""
    sentences: list[str] = []
    lines = []
    for line in text.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        if HEADING_LINE_RE.match(stripped):
            lines.append(stripped)
            continue
        for s in SENTENCE_END_RE.split(stripped):
            if s.strip():
                sentences.append(s.strip())
                lines.append(f"[{len(sentences)}] {s.strip()}")
    return "\n".join(lines), sentences
