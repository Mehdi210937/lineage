"""Couche brute dans DuckDB : on stocke les réponses API telles quelles (JSON).

Toute la mise en forme se fait ensuite dans dbt. Une seule table générique
`raw.api_responses`, idempotente sur (source, entity_type, entity_id).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import duckdb

SCHEMA_SQL = """
create schema if not exists raw;

create table if not exists raw.api_responses (
    source       varchar not null,   -- musicbrainz | discogs | wikidata
    entity_type  varchar not null,   -- artist | artist_releases | artist_search | influences ...
    entity_id    varchar not null,
    payload      json,
    fetched_at   timestamp not null,
    primary key (source, entity_type, entity_id)
);

create table if not exists raw.seed_artists (
    seed_name    varchar primary key,
    note         varchar,
    mbid         varchar,
    match_score  integer,
    matched_name varchar,
    resolved_at  timestamp
);

create table if not exists raw.crawl_queue (
    mbid            varchar primary key,
    name            varchar,
    depth           integer not null,
    discovered_from varchar,
    discovered_via  varchar,
    status          varchar not null default 'pending',  -- pending | done | error
    error           varchar,
    updated_at      timestamp not null
);

-- Migration douce des bases existantes : seuil de sorties utilisé lors de l'expansion par labels
-- de cet artiste (null = labels pas encore explorés).
alter table raw.crawl_queue add column if not exists label_expansion_limit integer;
-- Voisins MusicBrainz / Wikidata déjà ajoutés à la file (null = pas encore, ex. artiste traité
-- à la profondeur max d'un run précédent).
alter table raw.crawl_queue add column if not exists relations_expanded boolean;

-- Relations extraites des bios par le LLM (src/lineage/extract/run.py). Une ligne sans other_artist
-- marque un artiste traité sans relation trouvée.
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


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class RawStore:
    def __init__(self, path: Path | str):
        self.con = duckdb.connect(str(path))
        self.con.execute(SCHEMA_SQL)

    # --- réponses API -------------------------------------------------
    def is_fresh(self, source: str, entity_type: str, entity_id: str, max_age_days: int) -> bool:
        row = self.con.execute(
            "select fetched_at from raw.api_responses where source=? and entity_type=? and entity_id=?",
            [source, entity_type, entity_id],
        ).fetchone()
        return bool(row) and row[0] >= _now() - timedelta(days=max_age_days)

    def get(self, source: str, entity_type: str, entity_id: str) -> dict | None:
        row = self.con.execute(
            "select payload from raw.api_responses where source=? and entity_type=? and entity_id=?",
            [source, entity_type, entity_id],
        ).fetchone()
        return json.loads(row[0]) if row and row[0] is not None else None

    def put(self, source: str, entity_type: str, entity_id: str, payload: dict | None) -> None:
        self.con.execute(
            """
            insert or replace into raw.api_responses (source, entity_type, entity_id, payload, fetched_at)
            values (?, ?, ?, ?, ?)
            """,
            [source, entity_type, entity_id, json.dumps(payload) if payload is not None else None, _now()],
        )

    # --- artistes de départ ------------------------------------------
    def upsert_seed(self, seed_name: str, note: str | None, mbid: str | None, score: int | None, matched: str | None):
        self.con.execute(
            "insert or replace into raw.seed_artists values (?, ?, ?, ?, ?, ?)",
            [seed_name, note, mbid, score, matched, _now()],
        )

    # --- file de crawl -----------------------------------------------
    def enqueue(self, mbid: str, name: str | None, depth: int, discovered_from: str | None, via: str | None) -> bool:
        """Ajoute un artiste s'il n'est pas déjà connu. Renvoie True s'il est nouveau."""
        exists = self.con.execute("select 1 from raw.crawl_queue where mbid=?", [mbid]).fetchone()
        if exists:
            return False
        self.con.execute(
            """
            insert into raw.crawl_queue (mbid, name, depth, discovered_from, discovered_via, status, error, updated_at)
            values (?, ?, ?, ?, ?, 'pending', null, ?)
            """,
            [mbid, name, depth, discovered_from, via, _now()],
        )
        return True

    def next_pending(self, max_depth: int) -> tuple[str, str, int] | None:
        return self.con.execute(
            """
            select mbid, name, depth from raw.crawl_queue
            where status = 'pending' and depth <= ?
            order by depth, updated_at limit 1
            """,
            [max_depth],
        ).fetchone()

    def mark(self, mbid: str, status: str, error: str | None = None) -> None:
        self.con.execute(
            "update raw.crawl_queue set status=?, error=?, updated_at=? where mbid=?",
            [status, error, _now(), mbid],
        )

    def set_relations_expanded(self, mbid: str) -> None:
        self.con.execute("update raw.crawl_queue set relations_expanded=true where mbid=?", [mbid])

    def needing_relation_expansion(self, max_depth: int) -> list[tuple[str, str, int]]:
        """Artistes déjà traités dont les voisins n'ont pas été ajoutés (traités à la profondeur max)."""
        return self.con.execute(
            """
            select mbid, name, depth from raw.crawl_queue
            where status = 'done' and depth < ? and not coalesce(relations_expanded, false)
            order by depth, updated_at
            """,
            [max_depth],
        ).fetchall()

    def set_label_expansion(self, mbid: str, max_releases: int) -> None:
        self.con.execute("update raw.crawl_queue set label_expansion_limit=? where mbid=?", [max_releases, mbid])

    def needing_label_expansion(self, max_depth: int, max_releases: int) -> list[tuple[str, str, int]]:
        """Artistes déjà traités dont les labels n'ont pas été explorés (ou avec un seuil plus bas)."""
        return self.con.execute(
            """
            select mbid, name, depth from raw.crawl_queue
            where status = 'done' and depth < ?
              and (label_expansion_limit is null or label_expansion_limit < ?)
            order by depth, updated_at
            """,
            [max_depth, max_releases],
        ).fetchall()

    def requeue_errors(self) -> int:
        """Remet en attente les artistes en erreur (ex. API saturée au run précédent). Renvoie leur nombre."""
        n = self.count("error")
        self.con.execute("update raw.crawl_queue set status='pending', updated_at=? where status='error'", [_now()])
        return n

    def count(self, status: str | None = None) -> int:
        if status:
            return self.con.execute("select count(*) from raw.crawl_queue where status=?", [status]).fetchone()[0]
        return self.con.execute("select count(*) from raw.crawl_queue").fetchone()[0]

    def close(self) -> None:
        self.con.close()
