"""Helpers partagés par les pages : chemin de la base, cache des requêtes, sélecteurs d'artistes."""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

from lineage.explore import db as xdb

SOURCE_COLORS = {"musicbrainz": "#4C78A8", "wikidata": "#54A24B", "llm": "#E45756"}
SOURCE_LABELS = {"musicbrainz": "MusicBrainz", "wikidata": "Wikidata", "llm": "LLM (bio Discogs)"}


def db_path() -> Path:
    return xdb.resolve_db_path(sys.argv[1:])


def db_version() -> float:
    """Date de modification de la base : invalide le cache après un `dbt build`."""
    try:
        return db_path().stat().st_mtime
    except OSError:
        return 0.0


@st.cache_data(show_spinner=False)
def _artists(path: str, version: float) -> list[dict]:
    return xdb.list_artists(path)


def artists() -> list[dict]:
    return _artists(str(db_path()), db_version())


def artist_label(a: dict) -> str:
    extra = ", ".join(x for x in (a["country_code"], "seed" if a["is_seed"] else None) if x)
    return f"{a['artist_name']} ({extra})" if extra else a["artist_name"]


def artist_options() -> tuple[list[str], dict[str, str]]:
    """(MBID triés, MBID → libellé) pour les sélecteurs."""
    rows = artists()
    labels = {a["artist_mbid"]: artist_label(a) for a in rows}
    return [a["artist_mbid"] for a in rows], labels
