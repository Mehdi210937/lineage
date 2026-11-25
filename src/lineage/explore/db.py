"""Accès en lecture seule aux marts DuckDB.

Chaque requête ouvre puis referme sa connexion : l'app ne garde jamais le fichier ouvert, ce qui
laisse le crawler ou `dbt build` écrire dans la base entre deux requêtes.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import duckdb

DEFAULT_DB = Path("data/lineage.duckdb")


def resolve_db_path(argv: list[str] | None = None) -> Path:
    """`--db chemin` (après `--` avec streamlit run), sinon LINEAGE_APP_DB, sinon data/lineage.duckdb."""
    argv = argv or []
    if "--db" in argv:
        i = argv.index("--db")
        if i + 1 < len(argv):
            return Path(argv[i + 1])
    return Path(os.getenv("LINEAGE_APP_DB", str(DEFAULT_DB)))


def query(db_path: Path | str, sql: str, params: list[Any] | None = None) -> list[dict[str, Any]]:
    with duckdb.connect(str(db_path), read_only=True) as con:
        cur = con.execute(sql, params or [])
        cols = [d[0] for d in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]


def list_artists(db_path: Path | str) -> list[dict[str, Any]]:
    return query(
        db_path,
        """
        select artist_mbid, artist_name, country_code, is_seed, crawl_depth,
               discogs_profile is not null as has_bio
        from marts.dim_artists
        order by is_seed desc, artist_name
        """,
    )


def artist_details(db_path: Path | str, mbid: str) -> dict[str, Any] | None:
    rows = query(db_path, "select * from marts.dim_artists where artist_mbid = ?", [mbid])
    return rows[0] if rows else None
