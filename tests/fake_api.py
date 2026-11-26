"""Fausse API avec des artistes FICTIFS, pour tester le crawl et dbt sans réseau.

Les formes des payloads imitent celles des vraies API (MusicBrainz, Discogs, Wikidata).
"""
from __future__ import annotations

A = "00000000-0000-0000-0000-00000000000a"  # Test Coven (seed)
B = "00000000-0000-0000-0000-00000000000b"  # Ghost Ritual (membre)
C = "00000000-0000-0000-0000-00000000000c"  # Old Drone Master (influence Wikidata)
D = "00000000-0000-0000-0000-00000000000d"  # Far Away Band (profondeur 2)
E = "00000000-0000-0000-0000-00000000000e"  # Crypt Sister (même label de niche que A)
F = "00000000-0000-0000-0000-00000000000f"  # Pop Star (seulement sur le gros label)
G = "00000000-0000-0000-0000-000000000010"  # Veil Choir (label de niche de E, profondeur 2)
LABEL = "11111111-1111-1111-1111-111111111111"      # Crypt Records : label de la scène (A et B, du noyau)
BIG_LABEL = "22222222-2222-2222-2222-222222222222"  # Mega Major : 5000 sorties, à ne pas explorer
HOLLOW = "33333333-3333-3333-3333-333333333333"     # Hollow Tapes : label de niche récent de E
VARIOUS = "89ad4ac3-39f7-470e-963a-56509c546377"    # vrai MBID de « Various Artists »
NO_LABEL = "157afde4-4bf5-4039-8ad2-5a15acc85176"  # vrai MBID de [no label] dans MusicBrainz


# Bio Discogs fictive de Test Coven, avec du balisage Discogs comme les vraies bios ; dbt le retire
# (macro clean_discogs_markup) avant l'envoi au LLM et la vérification des citations.
TEST_COVEN_BIO = (
    "Duo formé en 2008, très influencé par Old Drone Master et la scène chopped and screwed. "
    "Leur premier EP a été enregistré avec [a=Ghost Ritual]. "
    "Souvent comparé à Crypt Sister pour ses voix noyées d'écho. "
    "Leur son a profondément marqué Far Away Band. "
    "Le titre [i]Hex[/i] sample un vieux disque de Lady Midnight."
)
TEST_COVEN_BIO_CLEAN = (
    "Duo formé en 2008, très influencé par Old Drone Master et la scène chopped and screwed. "
    "Leur premier EP a été enregistré avec Ghost Ritual. "
    "Souvent comparé à Crypt Sister pour ses voix noyées d'écho. "
    "Leur son a profondément marqué Far Away Band. "
    "Le titre Hex sample un vieux disque de Lady Midnight."
)

# Article Wikipédia fictif de Test Coven (texte brut avec sections, comme l'API TextExtracts).
TEST_COVEN_WIKIPEDIA = """Test Coven is a fictional American witch house duo formed in 2008.

== History ==
The duo met in Traverse City and released the Hex EP on Crypt Records in 2010.

== Musical style and influences ==
Test Coven cite Old Drone Master as their main influence. Their slowed-down vocals are often compared to Crypt Sister. Reviewers also hear the minimal techno of Nobody Known.

== Discography ==
Hex EP (2010)"""

# Ce que l'extracteur (v2, après ses filtres) pourrait tirer de ces textes, pour la base de démo :
# (artiste, autre artiste tel qu'écrit par le LLM, relation, citation, confiance, texte source)
DEMO_LLM_CLAIMS = [
    (A, "Old Drone Master", "influenced_by", "très influencé par Old Drone Master", 0.9, "discogs"),
    (A, "The Crypt-Sister", "compared_to", "Souvent comparé à Crypt Sister", 0.6, "discogs"),
    (A, "Far Away Band", "influenced", "Leur son a profondément marqué Far Away Band.", 0.6, "discogs"),
    (A, "Lady Midnight", "sampled", "sample un vieux disque de Lady Midnight", 0.9, "discogs"),  # hors crawl
    (A, "Old Drone Master", "influenced_by", "Test Coven cite Old Drone Master as their main influence.",
     0.9, "wikipedia"),
    (A, "Nobody Known", "compared_to", "the minimal techno of Nobody Known", 0.6, "wikipedia"),  # hors crawl
    # texte traité, aucune relation trouvée
    (B, None, None, None, None, "discogs"),
]


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
    E: {"id": E, "name": "Crypt Sister", "sort-name": "Crypt Sister", "type": "Person", "country": "US",
        "life-span": {"begin": "1991", "end": None, "ended": False},
        "genres": [{"name": "witch house", "count": 2}], "tags": [], "relations": []},
    F: {"id": F, "name": "Pop Star", "sort-name": "Star, Pop", "type": "Person", "country": "GB",
        "life-span": {"begin": "2005", "end": None, "ended": False},
        "genres": [{"name": "pop", "count": 4}], "tags": [], "relations": []},
    G: {"id": G, "name": "Veil Choir", "sort-name": "Veil Choir", "type": "Group", "country": "DE",
        "life-span": {"begin": "2022", "end": None, "ended": False},
        "genres": [{"name": "darkwave", "count": 1}], "tags": [], "relations": []},
}


def _credit(mbid, name):
    return [{"name": name, "joinphrase": "", "artist": {"id": mbid, "name": name}}]


def _label_info(label_mbid, name, catno=None):
    return [{"catalog-number": catno, "label": {"id": label_mbid, "name": name}}]


# Sorties des labels, au format de /ws/2/release?label=...&inc=artist-credits
LABEL_RELEASES = {
    LABEL: {"release-count": 4, "releases": [
        {"id": "r1", "title": "Hex EP", "artist-credit": _credit(A, "Test Coven")},
        {"id": "r11", "title": "Ritual Tape", "artist-credit": _credit(B, "Ghost Ritual")},
        {"id": "r5", "title": "Crypt Sister LP", "artist-credit": _credit(E, "Crypt Sister")},
        {"id": "r6", "title": "Crypt Sampler", "artist-credit": _credit(VARIOUS, "Various Artists")},
    ]},
    HOLLOW: {"release-count": 3, "releases": [
        {"id": "r8", "title": "Drowned Hymns", "artist-credit": _credit(E, "Crypt Sister")},
        {"id": "r9", "title": "Static Bloom", "artist-credit": _credit(D, "Far Away Band")},
        {"id": "r10", "title": "Veil Choir I", "artist-credit": _credit(G, "Veil Choir")},
    ]},
    BIG_LABEL: {"release-count": 5000, "releases": [
        {"id": "r4", "title": "Big Single", "artist-credit": _credit(A, "Test Coven")},
        {"id": "r7", "title": "Chart Hit", "artist-credit": _credit(F, "Pop Star")},
    ]},
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
        if mbid == E:
            return {"artist_mbid": mbid, "release-count": 2, "releases": [
                {"id": "r5", "title": "Crypt Sister LP", "date": "2012", "status": "Official",
                 "label-info": _label_info(LABEL, "Crypt Records", "CRYPT002")},
                {"id": "r8", "title": "Drowned Hymns", "date": "2025-10", "status": "Official",
                 "label-info": _label_info(HOLLOW, "Hollow Tapes", "HT-01")},
            ]}
        if mbid == D:
            return {"artist_mbid": mbid, "release-count": 1, "releases": [
                {"id": "r9", "title": "Static Bloom", "date": "2026", "status": "Official",
                 "label-info": _label_info(HOLLOW, "Hollow Tapes", "HT-02")},
            ]}
        if mbid == G:
            return {"artist_mbid": mbid, "release-count": 1, "releases": [
                {"id": "r10", "title": "Veil Choir I", "date": "2026", "status": "Official",
                 "label-info": _label_info(HOLLOW, "Hollow Tapes", "HT-03")},
            ]}
        if mbid == B:  # Ghost Ritual (noyau) sort aussi sur Crypt Records : le label est partagé
            return {"artist_mbid": mbid, "release-count": 1, "releases": [
                {"id": "r11", "title": "Ritual Tape", "date": "2011", "status": "Official",
                 "label-info": _label_info(LABEL, "Crypt Records", "CRYPT003")},
            ]}
        if mbid == F:
            return {"artist_mbid": mbid, "release-count": 1, "releases": [
                {"id": "r7", "title": "Chart Hit", "date": "2024", "status": "Official",
                 "label-info": _label_info(BIG_LABEL, "Mega Major", "MM-2")},
            ]}
        if mbid != A:
            return {"artist_mbid": mbid, "release-count": 0, "releases": []}
        return {"artist_mbid": mbid, "release-count": 4, "releases": [
            {"id": "r1", "title": "Hex EP", "date": "2010-03-01", "status": "Official",
             "label-info": [{"catalog-number": "CRYPT001", "label": {"id": LABEL, "name": "Crypt Records"}}]},
            {"id": "r2", "title": "Self Release", "date": "2011", "status": "Official",
             "label-info": [{"catalog-number": None, "label": {"id": NO_LABEL, "name": "[no label]"}}]},
            {"id": "r3", "title": "Unknown Tape", "date": "2012", "status": "Official", "label-info": []},
            {"id": "r4", "title": "Big Single", "date": "2013", "status": "Official",
             "label-info": _label_info(BIG_LABEL, "Mega Major", "MM-1")},
        ]}

    def mb_label_releases(self, label_mbid, max_releases):
        """Imite musicbrainz.browse_label_releases : payload tronqué si le label dépasse le seuil."""
        self.calls.append(f"label:{label_mbid}")
        data = LABEL_RELEASES.get(label_mbid, {"release-count": 0, "releases": []})
        complete = data["release-count"] <= max_releases
        return {"label_mbid": label_mbid, "release-count": data["release-count"], "complete": complete,
                "releases": data["releases"] if complete else []}

    def discogs_artist(self, discogs_id):
        self.calls.append(f"discogs:{discogs_id}")
        return {"id": int(discogs_id), "name": "Test Coven",
                "profile": TEST_COVEN_BIO,
                "data_quality": "Needs Vote"}

    def discogs_releases(self, discogs_id):
        return {"discogs_artist_id": discogs_id, "releases": [
            {"id": 5001, "title": "Hex EP", "year": 2010, "type": "release", "role": "Main", "label": "Crypt Records"},
        ]}

    def wikidata_sitelinks(self, qid):
        self.calls.append(f"sitelinks:{qid}")
        return {"qid": qid, "enwiki": "Test Coven (band)" if qid == "Q900001" else None}

    def wikipedia_article(self, title):
        self.calls.append(f"wikipedia:{title}")
        if title != "Test Coven (band)":
            return None
        return {"title": title, "pageid": 4242, "extract": TEST_COVEN_WIKIPEDIA}

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
