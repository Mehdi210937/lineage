"""API Discogs : https://www.discogs.com/developers"""
from __future__ import annotations

from lineage.http import PoliteClient

BASE = "https://api.discogs.com"
MAX_RELEASE_PAGES = 3


def _auth(token: str) -> dict:
    return {"Authorization": f"Discogs token={token}"}


def get_artist(client: PoliteClient, discogs_id: str, token: str) -> dict | None:
    """Fiche artiste, dont le champ `profile` (bio en texte libre) utilisé par le LLM."""
    return client.get_json(f"{BASE}/artists/{discogs_id}", headers=_auth(token))


def get_artist_releases(client: PoliteClient, discogs_id: str, token: str) -> dict:
    releases: list[dict] = []
    for page in range(1, MAX_RELEASE_PAGES + 1):
        data = client.get_json(
            f"{BASE}/artists/{discogs_id}/releases",
            params={"per_page": 100, "page": page, "sort": "year"},
            headers=_auth(token),
        )
        if not data:
            break
        releases.extend(data.get("releases", []))
        pages = (data.get("pagination") or {}).get("pages", 1)
        if page >= pages:
            break
    return {"discogs_artist_id": discogs_id, "releases": releases}
