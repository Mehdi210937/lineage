"""API MusicBrainz : https://musicbrainz.org/doc/MusicBrainz_API"""
from __future__ import annotations

import re

from lineage.http import PoliteClient

BASE = "https://musicbrainz.org/ws/2"
ARTIST_INC = "aliases+tags+genres+artist-rels+label-rels+url-rels"
MAX_RELEASE_PAGES = 3  # 300 sorties max par artiste, largement assez pour la scène

DISCOGS_ARTIST_RE = re.compile(r"discogs\.com/artist/(\d+)")
WIKIDATA_RE = re.compile(r"wikidata\.org/wiki/(Q\d+)")


def search_artist(client: PoliteClient, name: str) -> dict | None:
    # Les guillemets imposent une recherche sur le nom exact (avec tolérance Lucene).
    safe = name.replace('"', "")
    return client.get_json(f"{BASE}/artist", params={"query": f'artist:"{safe}"', "fmt": "json", "limit": 5})


def get_artist(client: PoliteClient, mbid: str) -> dict | None:
    return client.get_json(f"{BASE}/artist/{mbid}", params={"inc": ARTIST_INC, "fmt": "json"})


def browse_releases(client: PoliteClient, mbid: str) -> dict:
    """Toutes les sorties d'un artiste avec leurs labels, regroupées dans un seul payload."""
    releases: list[dict] = []
    total = 0
    for page in range(MAX_RELEASE_PAGES):
        data = client.get_json(
            f"{BASE}/release",
            params={"artist": mbid, "inc": "labels", "limit": 100, "offset": page * 100, "fmt": "json"},
        )
        if not data:
            break
        batch = data.get("releases", [])
        total = data.get("release-count", len(batch))
        releases.extend(batch)
        if len(releases) >= total or not batch:
            break
    return {"artist_mbid": mbid, "release-count": total, "releases": releases}


def best_match(search_payload: dict | None, name: str) -> tuple[str, str, int] | None:
    """Choisit le meilleur résultat de recherche : nom identique d'abord, puis score."""
    if not search_payload:
        return None
    candidates = search_payload.get("artists", [])
    if not candidates:
        return None
    exact = [a for a in candidates if a.get("name", "").casefold() == name.casefold()]
    pick = max(exact or candidates, key=lambda a: int(a.get("score", 0)))
    return pick["id"], pick.get("name", ""), int(pick.get("score", 0))


def external_ids(artist_payload: dict) -> dict[str, str]:
    """Extrait les identifiants Discogs et Wikidata depuis les relations URL."""
    ids: dict[str, str] = {}
    for rel in artist_payload.get("relations", []):
        if rel.get("target-type") != "url":
            continue
        resource = (rel.get("url") or {}).get("resource", "")
        if m := DISCOGS_ARTIST_RE.search(resource):
            ids.setdefault("discogs", m.group(1))
        elif m := WIKIDATA_RE.search(resource):
            ids.setdefault("wikidata", m.group(1))
    return ids


def related_artists(artist_payload: dict) -> list[tuple[str, str, str]]:
    """(mbid, nom, type de relation) pour chaque artiste lié."""
    out = []
    for rel in artist_payload.get("relations", []):
        if rel.get("target-type") == "artist" and rel.get("artist"):
            out.append((rel["artist"]["id"], rel["artist"].get("name", ""), rel.get("type", "")))
    return out
