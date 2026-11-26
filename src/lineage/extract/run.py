"""Extraction des influences avec un LLM local (Ollama), depuis deux textes par artiste :
l'article Wikipédia (en entier, style et influences d'abord, découpé en morceaux) et la bio Discogs
nettoyée. Chaque morceau est envoyé en phrases numérotées : le LLM renvoie des numéros de phrases,
et la citation stockée est la phrase originale.

Pré-requis : Ollama installé et le modèle téléchargé (`ollama pull qwen3:8b`),
puis `dbt build` lancé au moins une fois pour que marts.dim_artists existe.

    python -m lineage.extract.run              # tous les textes pas encore traités par cette version
    python -m lineage.extract.run --limit 20

Seuls les artistes du périmètre sont traités. Chaque texte n'est traité qu'une fois par version de
l'extracteur (EXTRACTOR_VERSION) : la changer relance tout, et dbt ne garde que le dernier passage.
"""
from __future__ import annotations

import argparse
import json
import logging
from collections import Counter
from datetime import datetime, timezone

import duckdb
import httpx

from lineage.config import get_settings
from lineage.extract.schema import (
    MB_IDENTITY_EDGE_TYPES,
    SYSTEM_PROMPT,
    ExtractionResult,
    LLMResult,
    filter_claims,
    normalize_name,
    resolve_sentences,
)
from lineage.extract.texts import numbered, text_chunks
from lineage.store import SCHEMA_SQL

log = logging.getLogger("lineage.extract")

# 2 : Wikipédia, bio nettoyée, filtres genres / membres
# 3 : article Wikipédia en entier (par morceaux), numéros de phrases au lieu de citations recopiées
EXTRACTOR_VERSION = "3"
TEXT_LABELS = {"wikipedia": "Article Wikipédia", "discogs": "Biographie Discogs"}


def ask_llm(client: httpx.Client, url: str, model: str, artist_name: str, numbered_text: str, kind: str) -> LLMResult:
    resp = client.post(
        f"{url}/api/chat",
        json={
            "model": model,
            "stream": False,
            "think": False,                                   # qwen3 : réponse directe, plus rapide
            "format": LLMResult.model_json_schema(),         # sortie contrainte au schéma
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Artiste : {artist_name}\n\n{TEXT_LABELS[kind]} :\n{numbered_text}"},
            ],
        },
        timeout=300,
    )
    resp.raise_for_status()
    return LLMResult.model_validate_json(resp.json()["message"]["content"])


def extract(client: httpx.Client, url: str, model: str, artist_name: str, chunks: list[str], kind: str,
            ) -> tuple[ExtractionResult, Counter]:
    """Interroge le LLM morceau par morceau. Renvoie les affirmations, citations = phrases originales,
    et les rejets (numéro invalide, artiste absent de la phrase)."""
    claims, rejected = [], Counter()
    for chunk in chunks:
        text, sentences = numbered(chunk)
        if not sentences:
            continue
        found, bad = resolve_sentences(ask_llm(client, url, model, artist_name, text, kind), sentences)
        claims.extend(found)
        rejected.update(bad)
    return ExtractionResult(claims=claims), rejected


def texts_to_process(con: duckdb.DuckDBPyConnection, model: str, limit: int | None) -> list[tuple]:
    """(mbid, nom, type de texte, morceaux à envoyer) pas encore traités par cette version."""
    rows = con.execute(
        """
        with texts as (
            select artist_mbid, artist_name, crawl_depth, 'wikipedia' as kind, wikipedia_text as body
            from marts.dim_artists where in_perimeter and wikipedia_text is not null
            union all
            select artist_mbid, artist_name, crawl_depth, 'discogs', discogs_profile_clean
            from marts.dim_artists where in_perimeter and discogs_profile_clean is not null
        )
        select t.artist_mbid, t.artist_name, t.kind, t.body
        from texts t
        where not exists (
            select 1 from raw.llm_influence_claims c
            where c.artist_mbid = t.artist_mbid and c.model = ? and c.text_source = t.kind
              and c.extractor_version = ?
        )
        order by t.crawl_depth nulls last, t.artist_name, t.kind desc
        """,
        [model, EXTRACTOR_VERSION],
    ).fetchall()
    out = [(m, n, k, text_chunks(k, b)) for m, n, k, b in rows]
    return out[:limit] if limit else out


def known_context(con: duckdb.DuckDBPyConnection) -> tuple[set[str], dict[str, set[str]]]:
    """Genres et tags connus (normalisés), et pour chaque artiste les noms de ses membres / alias /
    collaborations MusicBrainz."""
    genres = {
        normalize_name(g)
        for (g,) in con.execute(
            "select distinct unnest(coalesce(genres, []) || coalesce(tags, [])) from marts.dim_artists"
        ).fetchall()
        if g
    }
    identity: dict[str, set[str]] = {}
    for a, b, name_a, name_b in con.execute(
        """
        select from_artist_mbid, to_artist_mbid, from_artist_name, to_artist_name
        from marts.edges_artists
        where source_system = 'musicbrainz' and edge_type in (select unnest(?))
        """,
        [sorted(MB_IDENTITY_EDGE_TYPES)],
    ).fetchall():
        if name_b:
            identity.setdefault(a, set()).add(normalize_name(name_b))
        if name_a:
            identity.setdefault(b, set()).add(normalize_name(name_a))
    return genres, identity


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="nombre max de textes à traiter")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    s = get_settings()
    con = duckdb.connect(str(s.duckdb_path))
    con.execute(SCHEMA_SQL)
    todo = texts_to_process(con, s.ollama_model, args.limit)
    genres, identity = known_context(con)
    log.info("%s texte(s) à traiter avec %s (extracteur v%s)", len(todo), s.ollama_model, EXTRACTOR_VERSION)

    total_kept, total_rejected = 0, Counter()
    with httpx.Client() as client:
        for i, (mbid, name, kind, chunks) in enumerate(todo, 1):
            try:
                raw, rejected = extract(client, s.ollama_url, s.ollama_model, name, chunks, kind)
            except Exception:
                log.exception("Échec de l'extraction pour %s (%s)", name, kind)
                continue
            result, filtered = filter_claims(raw, "\n".join(chunks), name, genres, identity.get(mbid, set()))
            rejected.update(filtered)
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            rows = [
                (mbid, s.ollama_model, c.other_artist, c.relation, c.evidence_quote, c.confidence, now,
                 kind, EXTRACTOR_VERSION)
                for c in result.claims
            ]
            # une ligne « vide » marque le texte comme traité même sans relation trouvée
            rows = rows or [(mbid, s.ollama_model, None, None, None, None, now, kind, EXTRACTOR_VERSION)]
            con.executemany(
                """
                insert into raw.llm_influence_claims
                    (artist_mbid, model, other_artist, relation, evidence_quote, confidence, extracted_at,
                     text_source, extractor_version)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )
            total_kept += len(result.claims)
            total_rejected.update(rejected)
            log.info("[%s/%s] %s (%s, %s morceau(x)) : %s gardée(s), %s rejetée(s) %s", i, len(todo), name,
                     kind, len(chunks), len(result.claims), sum(rejected.values()), dict(rejected) or "")
            log.debug(json.dumps(raw.model_dump(), ensure_ascii=False))
    log.info("Terminé : %s affirmation(s) gardée(s) ; rejets : %s", total_kept, dict(total_rejected))
    con.close()


if __name__ == "__main__":
    main()
