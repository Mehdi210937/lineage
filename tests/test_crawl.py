from fake_api import A, B, C, D, FakeApi

from lineage.crawl import Crawler
from lineage.sources import musicbrainz
from lineage.store import RawStore


def make(tmp_path):
    store = RawStore(tmp_path / "test.duckdb")
    api = FakeApi()
    return Crawler(api, store, refresh_days=30), store, api


def test_external_ids():
    from fake_api import ARTISTS
    assert musicbrainz.external_ids(ARTISTS[A]) == {"discogs": "1001", "wikidata": "Q900001"}


def test_crawl_respects_depth(tmp_path):
    crawler, store, _ = make(tmp_path)
    crawler.resolve_seeds([{"name": "Test Coven", "note": "test"}])
    crawler.run(max_depth=1, max_artists=50)
    done = {r[0] for r in store.con.execute("select mbid from raw.crawl_queue where status='done'").fetchall()}
    # A (seed) + B (membre) + C (influence Wikidata) ; D est à profondeur 2 donc non traité
    assert done == {A, B, C}
    depth_d = store.con.execute("select depth, status from raw.crawl_queue where mbid=?", [D]).fetchone()
    assert depth_d is None  # B est à profondeur max, on n'ajoute pas ses voisins


def test_crawl_is_cached(tmp_path):
    crawler, store, api = make(tmp_path)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=2, max_artists=50)
    first = len(api.calls)
    # Deuxième run : tout est en cache, aucune nouvelle requête vers les artistes
    store.con.execute("update raw.crawl_queue set status='pending'")
    crawler.run(max_depth=2, max_artists=50)
    assert len(api.calls) == first


def test_unknown_seed_is_recorded(tmp_path):
    crawler, store, _ = make(tmp_path)
    crawler.resolve_seeds([{"name": "Does Not Exist", "note": None}])
    row = store.con.execute("select mbid from raw.seed_artists where seed_name='Does Not Exist'").fetchone()
    assert row == (None,)
