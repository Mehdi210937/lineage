"""Wikidata (SPARQL) : relations d'influence P737 dans les deux sens.

P737 = « influencé par ». P434 = identifiant d'artiste MusicBrainz, qui permet
de relier l'artiste influent au reste du graphe.
"""
from __future__ import annotations

import re

from lineage.http import PoliteClient

ENDPOINT = "https://query.wikidata.org/sparql"
QID_RE = re.compile(r"^Q\d+$")

QUERY = """
SELECT ?direction ?other ?otherLabel ?otherMbid WHERE {{
  {{ wd:{qid} wdt:P737 ?other . BIND("influenced_by" AS ?direction) }}
  UNION
  {{ ?other wdt:P737 wd:{qid} . BIND("influenced" AS ?direction) }}
  OPTIONAL {{ ?other wdt:P434 ?otherMbid . }}
  SERVICE wikibase:label {{ bd:serviceParam wikibase:language "en,fr". }}
}}
"""


def get_influences(client: PoliteClient, qid: str) -> dict | None:
    if not QID_RE.match(qid):
        raise ValueError(f"QID invalide : {qid}")
    data = client.get_json(
        ENDPOINT,
        params={"query": QUERY.format(qid=qid), "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
    )
    if data is None:
        return None
    return {"qid": qid, "bindings": data.get("results", {}).get("bindings", [])}


def related_artists(influences_payload: dict | None) -> list[tuple[str, str, str]]:
    """(mbid, nom, direction) pour les artistes influents/influencés qui ont un MBID."""
    out = []
    for b in (influences_payload or {}).get("bindings", []):
        mbid = (b.get("otherMbid") or {}).get("value")
        if mbid:
            out.append((mbid, (b.get("otherLabel") or {}).get("value", ""), b["direction"]["value"]))
    return out
