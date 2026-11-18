"""API MusicBrainz : https://musicbrainz.org/doc/MusicBrainz_API"""
from __future__ import annotations

import re

from lineage.http import PoliteClient

BASE = "https://musicbrainz.org/ws/2"
ARTIST_INC = "aliases+tags+genres+artist-rels+label-rels+url-rels"
MAX_RELEASE_PAGES = 3  # 300 sorties max par artiste, largement assez pour la scène

DISCOGS_ARTIST_RE = re.compile(r"discogs\.com/artist/(\d+)")
WIKIDATA_RE = re.compile(r"wikidata\.org/wiki/(Q\d+)")

# Entités « spéciales » de MusicBrainz, qui ne sont pas de vrais labels / artistes.
NO_LABEL_MBID = "157afde4-4bf5-4039-8ad2-5a15acc85176"         # [no label] = autoproduction
PLACEHOLDER_LABELS = {NO_LABEL_MBID}
VARIOUS_ARTISTS_MBID = "89ad4ac3-39f7-470e-963a-56509c546377"  # compilations
UNKNOWN_ARTIST_MBID = "125ec42a-7229-4250-afc5-e057484327fe"   # [unknown]
PLACEHOLDER_ARTISTS = {VARIOUS_ARTISTS_MBID, UNKNOWN_ARTIST_MBID}


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


def browse_label_releases(client: PoliteClient, label_mbid: str, max_releases: int) -> dict:
    """Sorties d'un label avec leurs artistes, si le label en a au plus `max_releases`.

    La première page donne le nombre total de sorties : au-delà du seuil (Polydor, Fiction...),
    on s'arrête là (une seule requête) et le payload est marqué incomplet.
    """
    releases: list[dict] = []
    total = 0
    page = 0
    while True:
        data = client.get_json(
            f"{BASE}/release",
            params={"label": label_mbid, "inc": "artist-credits", "limit": 100, "offset": page * 100, "fmt": "json"},
        )
        if not data:
            break
        batch = data.get("releases", [])
        total = data.get("release-count", len(batch))
        if total > max_releases:
            return {"label_mbid": label_mbid, "release-count": total, "complete": False, "releases": []}
        releases.extend(batch)
        page += 1
        if len(releases) >= total or not batch:
            break
    return {"label_mbid": label_mbid, "release-count": total, "complete": True, "releases": releases}


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


def artist_labels(artist_payload: dict | None, releases_payload: dict | None) -> dict[str, str]:
    """{mbid: nom} des vrais labels d'un artiste : label-info de ses sorties + relations label."""
    labels: dict[str, str] = {}
    for rel in (artist_payload or {}).get("relations", []):
        if rel.get("target-type") == "label" and rel.get("label"):
            labels.setdefault(rel["label"]["id"], rel["label"].get("name", ""))
    for release in (releases_payload or {}).get("releases", []):
        for info in release.get("label-info") or []:
            if label := info.get("label"):
                labels.setdefault(label["id"], label.get("name", ""))
    return {mbid: name for mbid, name in labels.items() if mbid not in PLACEHOLDER_LABELS}


def label_artists(label_payload: dict | None) -> list[tuple[str, str]]:
    """(mbid, nom) des artistes crédités sur les sorties d'un label, sans « Various Artists »."""
    seen: dict[str, str] = {}
    for release in (label_payload or {}).get("releases", []):
        for credit in release.get("artist-credit") or []:
            artist = credit.get("artist") or {}
            if (mbid := artist.get("id")) and mbid not in PLACEHOLDER_ARTISTS:
                seen.setdefault(mbid, artist.get("name", ""))
    return list(seen.items())
