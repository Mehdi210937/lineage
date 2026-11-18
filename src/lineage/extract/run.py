"""Phase 3 : extraction des influences depuis les bios Discogs avec un LLM local (Ollama).

Pré-requis : Ollama installé et le modèle téléchargé (`ollama pull qwen3:8b`),
puis `dbt build` lancé au moins une fois pour que marts.dim_artists existe.

    python -m lineage.extract.run --limit 20
"""
from __future__ import annotations

import argparse
import json
import logging
from datetime import datetime, timezone

import duckdb
import httpx

from lineage.config import get_settings
from lineage.extract.schema import SYSTEM_PROMPT, ExtractionResult, validate_against_source

log = logging.getLogger("lineage.extract")

DDL = """
create table if not exists raw.llm_influence_claims (
    artist_mbid    varchar,
    model          varchar,
    other_artist   varchar,
    relation       varchar,
    evidence_quote varchar,
    confidence     double,
    extracted_at   timestamp
);
"""


def extract(client: httpx.Client, url: str, model: str, artist_name: str, profile: str) -> ExtractionResult:
    resp = client.post(
        f"{url}/api/chat",
        json={
            "model": model,
            "stream": False,
            "format": ExtractionResult.model_json_schema(),  # sortie contrainte au schéma
            "options": {"temperature": 0},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Artiste : {artist_name}\n\nBiographie :\n{profile}"},
            ],
        },
        timeout=180,
    )
    resp.raise_for_status()
    raw = ExtractionResult.model_validate_json(resp.json()["message"]["content"])
    return validate_against_source(raw, profile)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=20)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    s = get_settings()
    con = duckdb.connect(str(s.duckdb_path))
    con.execute(DDL)
    todo = con.execute(
        """
        select a.artist_mbid, a.artist_name, a.discogs_profile
        from marts.dim_artists a
        where a.discogs_profile is not null
          and a.artist_mbid not in (select artist_mbid from raw.llm_influence_claims where model = ?)
        order by a.crawl_depth
        limit ?
        """,
        [s.ollama_model, args.limit],
    ).fetchall()

    with httpx.Client() as client:
        for mbid, name, profile in todo:
            try:
                result = extract(client, s.ollama_url, s.ollama_model, name, profile)
            except Exception:
                log.exception("Échec de l'extraction pour %s", name)
                continue
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            rows = [
                (mbid, s.ollama_model, c.other_artist, c.relation, c.evidence_quote, c.confidence, now)
                for c in result.claims
            ]
            # une ligne « vide » marque l'artiste comme traité même sans relation trouvée
            rows = rows or [(mbid, s.ollama_model, None, None, None, None, now)]
            con.executemany("insert into raw.llm_influence_claims values (?, ?, ?, ?, ?, ?, ?)", rows)
            log.info("%s : %s relation(s)", name, len(result.claims))
            log.debug(json.dumps(result.model_dump(), ensure_ascii=False))
    con.close()


if __name__ == "__main__":
    main()
