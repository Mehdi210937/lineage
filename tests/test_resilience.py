"""Une API saturée (503) ne doit jamais arrêter le crawl : on journalise, on marque, on continue."""
from __future__ import annotations

from fake_api import A, B, C, D, E, LABEL, FakeApi

from lineage.crawl import Crawler
from lineage.http import RetryableHTTPError
from lineage.store import RawStore


class FlakyApi(FakeApi):
    """Fausse API qui renvoie des 503 (après épuisement des reprises du client HTTP) sur certains appels."""

    def __init__(self, failing_labels=(), failing_artists=None, failing_searches=()):
        super().__init__()
        self.failing_labels = set(failing_labels)
        self.failing_artists = dict(failing_artists or {})  # mbid → nombre d'échecs restants
        self.failing_searches = set(failing_searches)

    def mb_label_releases(self, label_mbid, max_releases):
        if label_mbid in self.failing_labels:
            self.calls.append(f"label:{label_mbid}:503")
            raise RetryableHTTPError(f"503 https://musicbrainz.org/ws/2/release?label={label_mbid}")
        return super().mb_label_releases(label_mbid, max_releases)

    def mb_artist(self, mbid):
        if self.failing_artists.get(mbid, 0) > 0:
            self.failing_artists[mbid] -= 1
            raise RetryableHTTPError(f"503 https://musicbrainz.org/ws/2/artist/{mbid}")
        return super().mb_artist(mbid)

    def mb_search(self, name):
        if name in self.failing_searches:
            raise RetryableHTTPError("503 https://musicbrainz.org/ws/2/artist")
        return super().mb_search(name)


def make(tmp_path, api):
    store = RawStore(tmp_path / "test.duckdb")
    return Crawler(api, store, refresh_days=30), store


def status(store):
    return dict(store.con.execute("select mbid, status from raw.crawl_queue").fetchall())


def label_limit(store, mbid):
    return store.con.execute("select label_expansion_limit from raw.crawl_queue where mbid=?", [mbid]).fetchone()[0]


def test_label_503_does_not_stop_the_crawl(tmp_path):
    api = FlakyApi(failing_labels={LABEL})
    crawler, store = make(tmp_path, api)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=2, max_artists=50)  # ne doit pas lever

    s = status(store)
    assert s[A] == s[B] == s[C] == s[D] == "done"  # le reste du crawl a continué
    assert E not in s                               # E n'est joignable que par le label en erreur
    assert label_limit(store, A) is None            # labels de A à reprendre au prochain run
    assert crawler.failures >= 1

    # Au run suivant, MusicBrainz répond de nouveau : le label est repris et E découvert
    api.failing_labels.clear()
    crawler.run(max_depth=2, max_artists=50)
    s = status(store)
    assert s[E] == "done"
    assert label_limit(store, A) == crawler.label_max_releases


def test_label_503_during_catch_up_does_not_stop_the_crawl(tmp_path):
    """Le plantage d'origine : un 503 dans catch_up_label_expansion après avoir relevé --depth."""
    api = FlakyApi()
    crawler, store = make(tmp_path, api)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=0, max_artists=50)

    api.failing_labels = {LABEL}
    crawler.run(max_depth=1, max_artists=50)  # rattrapage des labels de A : 503 sur Crypt Records
    s = status(store)
    assert s[B] == s[C] == "done"  # les relations rattrapées ont été traitées malgré l'erreur
    assert E not in s
    assert f"label:{LABEL}:503" in api.calls

    api.failing_labels.clear()
    crawler.run(max_depth=1, max_artists=50)
    assert status(store)[E] == "done"


def test_other_labels_are_still_explored(tmp_path):
    """Un label en erreur n'empêche pas d'explorer les autres labels du même artiste."""
    from fake_api import HOLLOW, G

    api = FlakyApi(failing_labels={LABEL})
    crawler, store = make(tmp_path, api)
    store.enqueue(E, "Crypt Sister", 0, None, "seed")
    crawler.run(max_depth=1, max_artists=50)
    s = status(store)
    assert s[G] == "done"  # découvert par Hollow Tapes, l'autre label de E
    assert f"label:{HOLLOW}" in api.calls
    assert label_limit(store, E) is None


def test_failed_artist_is_retried_next_run(tmp_path):
    api = FlakyApi(failing_artists={B: 1})
    crawler, store = make(tmp_path, api)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=1, max_artists=50)
    assert status(store)[B] == "error"
    assert status(store)[C] == "done"
    error = store.con.execute("select error from raw.crawl_queue where mbid=?", [B]).fetchone()[0]
    assert error.startswith("RetryableHTTPError: 503")

    crawler.run(max_depth=1, max_artists=50)
    assert status(store)[B] == "done"


def test_failed_seed_search_is_skipped_then_retried(tmp_path):
    api = FlakyApi(failing_searches={"Test Coven"})
    crawler, store = make(tmp_path, api)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    assert store.count() == 0
    assert store.con.execute("select count(*) from raw.seed_artists").fetchone()[0] == 0

    api.failing_searches.clear()
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    assert status(store) == {A: "pending"}
