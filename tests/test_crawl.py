from fake_api import A, B, C, D, E, FakeApi

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
    # A (seed) + B (membre) + C (influence Wikidata) + E (même label de niche) ;
    # D est à profondeur 2 donc non traité
    assert done == {A, B, C, E}
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


def test_raising_depth_expands_already_crawled_artists(tmp_path):
    """B a été traité à la profondeur max (1) : relancer avec --depth 2 doit ajouter son voisin D,
    sans re-télécharger B."""
    crawler, store, api = make(tmp_path)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=1, max_artists=50)
    assert store.con.execute("select 1 from raw.crawl_queue where mbid=?", [D]).fetchone() is None

    before = len(api.calls)
    crawler.run(max_depth=2, max_artists=50)
    row = store.con.execute("select depth, discovered_via, status from raw.crawl_queue where mbid=?", [D]).fetchone()
    assert row == (2, "musicbrainz:collaboration", "done")
    assert f"artist:{B}" not in api.calls[before:]  # B vient du cache


def test_catch_up_uses_cached_wikidata_influences(tmp_path):
    """Seed traitée à la profondeur 0 : ses influences Wikidata (C) arrivent au run suivant, depuis le cache."""
    crawler, store, api = make(tmp_path)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=0, max_artists=50)

    before = len(api.calls)
    crawler.run(max_depth=1, max_artists=50)
    row = store.con.execute("select depth, discovered_via from raw.crawl_queue where mbid=?", [C]).fetchone()
    assert row == (1, "wikidata:influenced_by")
    new_calls = api.calls[before:]
    assert f"artist:{A}" not in new_calls and not any(c.startswith("wikidata:") for c in new_calls)


def test_catch_up_is_idempotent(tmp_path):
    crawler, store, api = make(tmp_path)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=2, max_artists=50)
    n_queue, n_calls = store.count(), len(api.calls)
    crawler.run(max_depth=2, max_artists=50)  # rien à rattraper, rien à traiter
    assert (store.count(), len(api.calls)) == (n_queue, n_calls)
    assert store.needing_relation_expansion(2) == []


def test_unknown_seed_is_recorded(tmp_path):
    crawler, store, _ = make(tmp_path)
    crawler.resolve_seeds([{"name": "Does Not Exist", "note": None}])
    row = store.con.execute("select mbid from raw.seed_artists where seed_name='Does Not Exist'").fetchone()
    assert row == (None,)
