"""Fausse API avec des artistes FICTIFS, pour tester le crawl et dbt sans réseau.

Les formes des payloads imitent celles des vraies API (MusicBrainz, Discogs, Wikidata).
"""
from __future__ import annotations

A = "00000000-0000-0000-0000-00000000000a"  # Test Coven (seed)
B = "00000000-0000-0000-0000-00000000000b"  # Ghost Ritual (membre)
C = "00000000-0000-0000-0000-00000000000c"  # Old Drone Master (influence Wikidata)
D = "00000000-0000-0000-0000-00000000000d"  # Far Away Band (profondeur 2)
LABEL = "11111111-1111-1111-1111-111111111111"


def _rel_artist(rel_type, mbid, name, direction="forward"):
    return {"type": rel_type, "direction": direction, "target-type": "artist",
            "begin": "2009", "end": None, "attributes": [],
            "artist": {"id": mbid, "name": name}}


def _rel_url(rel_type, url):
    return {"type": rel_type, "direction": "forward", "target-type": "url", "url": {"resource": url}}


ARTISTS = {
    A: {"id": A, "name": "Test Coven", "sort-name": "Test Coven", "type": "Group", "country": "US",
        "life-span": {"begin": "2008", "end": None, "ended": False},
        "genres": [{"name": "witch house", "count": 5}], "tags": [{"name": "dark", "count": 2}],
        "relations": [
            _rel_artist("member of band", B, "Ghost Ritual", direction="backward"),
            _rel_url("discogs", "https://www.discogs.com/artist/1001-Test-Coven"),
            _rel_url("wikidata", "https://www.wikidata.org/wiki/Q900001"),
            {"type": "recording contract", "direction": "forward", "target-type": "label",
             "label": {"id": LABEL, "name": "Crypt Records"}, "begin": "2010", "end": None},
        ]},
    B: {"id": B, "name": "Ghost Ritual", "sort-name": "Ritual, Ghost", "type": "Person", "country": "US",
        "life-span": {"begin": "1990", "end": None, "ended": False}, "genres": [], "tags": [],
        "relations": [
            _rel_artist("member of band", A, "Test Coven"),
            _rel_artist("collaboration", D, "Far Away Band"),
        ]},
    C: {"id": C, "name": "Old Drone Master", "sort-name": "Old Drone Master", "type": "Person",
        "country": "GB", "life-span": {"begin": "1950", "end": None, "ended": False},
        "genres": [{"name": "drone", "count": 3}], "tags": [], "relations": []},
    D: {"id": D, "name": "Far Away Band", "sort-name": "Far Away Band", "type": "Group",
        "country": "FR", "life-span": {}, "genres": [], "tags": [], "relations": []},
}


class FakeApi:
    has_discogs = True

    def __init__(self):
        self.calls: list[str] = []

    def mb_search(self, name):
        self.calls.append(f"search:{name}")
        hits = [a for a in ARTISTS.values() if a["name"].casefold() == name.casefold()]
        return {"artists": [{"id": a["id"], "name": a["name"], "score": 100} for a in hits]}

    def mb_artist(self, mbid):
        self.calls.append(f"artist:{mbid}")
        return ARTISTS.get(mbid)

    def mb_releases(self, mbid):
        self.calls.append(f"releases:{mbid}")
        if mbid != A:
            return {"artist_mbid": mbid, "release-count": 0, "releases": []}
        return {"artist_mbid": mbid, "release-count": 2, "releases": [
            {"id": "r1", "title": "Hex EP", "date": "2010-03-01", "status": "Official",
             "label-info": [{"catalog-number": "CRYPT001", "label": {"id": LABEL, "name": "Crypt Records"}}]},
            {"id": "r2", "title": "Self Release", "date": "2011", "status": "Official", "label-info": []},
        ]}

    def discogs_artist(self, discogs_id):
        self.calls.append(f"discogs:{discogs_id}")
        return {"id": int(discogs_id), "name": "Test Coven",
                "profile": "Duo formé en 2008, très influencé par Old Drone Master et la scène chopped and screwed.",
                "data_quality": "Needs Vote"}

    def discogs_releases(self, discogs_id):
        return {"discogs_artist_id": discogs_id, "releases": [
            {"id": 5001, "title": "Hex EP", "year": 2010, "type": "release", "role": "Main", "label": "Crypt Records"},
        ]}

    def wikidata_influences(self, qid):
        self.calls.append(f"wikidata:{qid}")
        return {"qid": qid, "bindings": [
            {"direction": {"type": "literal", "value": "influenced_by"},
             "other": {"type": "uri", "value": "http://www.wikidata.org/entity/Q900002"},
             "otherLabel": {"type": "literal", "value": "Old Drone Master"},
             "otherMbid": {"type": "literal", "value": C}},
            {"direction": {"type": "literal", "value": "influenced"},
             "other": {"type": "uri", "value": "http://www.wikidata.org/entity/Q900003"},
             "otherLabel": {"type": "literal", "value": "Some Unknown Kid"}},
        ]}
