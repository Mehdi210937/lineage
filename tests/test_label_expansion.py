"""Expansion du crawl par labels de niche."""
from fake_api import A, BIG_LABEL, E, F, LABEL, VARIOUS, FakeApi

from lineage.crawl import Crawler
from lineage.sources import musicbrainz
from lineage.store import RawStore


def make(tmp_path, label_max_releases=300):
    store = RawStore(tmp_path / "test.duckdb")
    api = FakeApi()
    return Crawler(api, store, refresh_days=30, label_max_releases=label_max_releases), store, api


def queue(store):
    rows = store.con.execute("select mbid, depth, discovered_via from raw.crawl_queue").fetchall()
    return {mbid: (depth, via) for mbid, depth, via in rows}


def label_calls(api):
    return [c for c in api.calls if c.startswith("label:")]


def test_niche_label_brings_its_artists(tmp_path):
    crawler, store, _ = make(tmp_path)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=1, max_artists=50)
    q = queue(store)
    assert q[E] == (1, "musicbrainz:label:Crypt Records")


def test_big_label_is_not_expanded(tmp_path):
    crawler, store, api = make(tmp_path)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=1, max_artists=50)
    assert F not in queue(store)
    # Le gros label n'a coûté qu'une requête, et son payload stocké est tronqué
    assert label_calls(api).count(f"label:{BIG_LABEL}") == 1
    payload = store.get("musicbrainz", "label_releases", BIG_LABEL)
    assert payload["complete"] is False and payload["releases"] == []


def test_placeholders_are_ignored(tmp_path):
    crawler, store, api = make(tmp_path)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=1, max_artists=50)
    assert f"label:{musicbrainz.NO_LABEL_MBID}" not in api.calls  # [no label] n'est pas un label
    assert VARIOUS not in queue(store)                             # Various Artists n'est pas un artiste


def test_threshold_is_configurable(tmp_path):
    crawler, store, _ = make(tmp_path, label_max_releases=10_000)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=1, max_artists=50)
    assert F in queue(store)


def test_zero_disables_expansion(tmp_path):
    crawler, store, api = make(tmp_path, label_max_releases=0)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=1, max_artists=50)
    assert label_calls(api) == []
    assert E not in queue(store)


def test_no_expansion_at_max_depth(tmp_path):
    crawler, store, api = make(tmp_path)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=0, max_artists=50)
    assert label_calls(api) == []
    assert set(queue(store)) == {A}


def test_shared_label_is_fetched_once_and_cached(tmp_path):
    crawler, store, api = make(tmp_path)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=2, max_artists=50)
    # A et E partagent Crypt Records : une seule requête pour ce label
    assert label_calls(api).count(f"label:{LABEL}") == 1
    first = len(api.calls)
    store.con.execute("update raw.crawl_queue set status='pending'")
    crawler.run(max_depth=2, max_artists=50)
    assert len(api.calls) == first


def test_existing_crawl_is_caught_up(tmp_path):
    """Une base crawlée avant l'expansion (ou avec 0) voit ses labels explorés au run suivant,
    sans re-télécharger les artistes déjà traités."""
    crawler, store, api = make(tmp_path, label_max_releases=0)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=1, max_artists=50)
    assert E not in queue(store)

    crawler.label_max_releases = 300
    before = len(api.calls)
    crawler.run(max_depth=1, max_artists=50)
    new_calls = api.calls[before:]
    assert queue(store)[E] == (1, "musicbrainz:label:Crypt Records")
    assert f"artist:{A}" not in new_calls  # A vient du cache


def test_raising_threshold_refetches_truncated_label(tmp_path):
    crawler, store, api = make(tmp_path)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=1, max_artists=50)
    assert F not in queue(store)

    crawler.label_max_releases = 10_000  # le payload tronqué en cache ne suffit plus
    crawler.run(max_depth=1, max_artists=50)
    assert label_calls(api).count(f"label:{BIG_LABEL}") == 2
    assert F in queue(store)


class StubClient:
    """Faux client HTTP pour tester la pagination de browse_label_releases."""

    def __init__(self, total):
        self.total = total
        self.requests = 0

    def get_json(self, url, params=None, headers=None):
        self.requests += 1
        start = params["offset"]
        n = max(0, min(params["limit"], self.total - start))
        return {"release-count": self.total,
                "releases": [{"id": f"r{start + i}", "artist-credit": []} for i in range(n)]}


def test_browse_label_stops_after_first_page_when_too_big():
    client = StubClient(total=5000)
    payload = musicbrainz.browse_label_releases(client, "x", max_releases=300)
    assert client.requests == 1
    assert payload == {"label_mbid": "x", "release-count": 5000, "complete": False, "releases": []}


def test_browse_label_paginates_small_labels():
    client = StubClient(total=250)
    payload = musicbrainz.browse_label_releases(client, "x", max_releases=300)
    assert client.requests == 3
    assert payload["complete"] is True and len(payload["releases"]) == 250


def test_artist_labels_skip_no_label():
    from fake_api import ARTISTS
    labels = musicbrainz.artist_labels(ARTISTS[A], FakeApi().mb_releases(A))
    assert labels == {LABEL: "Crypt Records", BIG_LABEL: "Mega Major"}
