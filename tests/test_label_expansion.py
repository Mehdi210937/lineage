"""Expansion du crawl par les labels de la scène, et recalcul du périmètre (--rescope).

Monde fictif : A (seed) et B (membre de A, donc du noyau) sortent sur Crypt Records (LABEL) ;
A sort aussi sur Mega Major (BIG_LABEL, 5000 sorties). E est découvert par Crypt Records ;
E sort aussi sur Hollow Tapes (HOLLOW) où sort G.
"""
from fake_api import A, B, BIG_LABEL, C, D, E, F, G, HOLLOW, LABEL, VARIOUS, FakeApi  # noqa: F401

from lineage.crawl import Crawler
from lineage.sources import musicbrainz
from lineage.store import RawStore


def make(tmp_path, label_max_releases=100, min_core=2):
    store = RawStore(tmp_path / "test.duckdb")
    api = FakeApi()
    crawler = Crawler(api, store, refresh_days=30, label_max_releases=label_max_releases,
                      label_min_core_artists=min_core)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    return crawler, store, api


def queue(store):
    rows = store.con.execute("select mbid, depth, discovered_via, status, scope from raw.crawl_queue").fetchall()
    return {mbid: (depth, via, status, scope) for mbid, depth, via, status, scope in rows}


def label_calls(api):
    return [c for c in api.calls if c.startswith("label:")]


def test_label_shared_by_two_core_artists_brings_its_artists(tmp_path):
    crawler, store, _ = make(tmp_path)
    crawler.run(max_depth=1, max_artists=50)
    q = queue(store)
    assert q[E][:4] == (1, "musicbrainz:label:Crypt Records", "done", "label")


def test_label_with_a_single_core_artist_is_not_explored(tmp_path):
    """Mega Major n'a qu'un artiste du noyau (A) : jamais téléchargé avec le seuil par défaut (2)."""
    crawler, store, api = make(tmp_path, label_max_releases=10_000)
    crawler.run(max_depth=2, max_artists=50)
    assert f"label:{BIG_LABEL}" not in api.calls
    assert F not in queue(store)

    (tmp_path / "x").mkdir()
    crawler, store, api = make(tmp_path / "x", label_max_releases=10_000, min_core=1)
    crawler.run(max_depth=1, max_artists=50)
    assert F in queue(store)


def test_label_artists_are_crawled_but_never_expanded(tmp_path):
    """E vient d'un label : ses données sont téléchargées, mais ni ses relations ni ses labels
    (Hollow Tapes) ne font entrer d'autres artistes."""
    crawler, store, api = make(tmp_path)
    crawler.run(max_depth=2, max_artists=50)
    q = queue(store)
    assert q[E][2:] == ("done", "label")
    assert f"artist:{E}" in api.calls and f"releases:{E}" in api.calls
    assert f"label:{HOLLOW}" not in api.calls
    assert G not in q
    assert q[D][3] == "core"  # D reste atteint par la collaboration de B (noyau)


def test_big_label_is_not_expanded(tmp_path):
    crawler, store, api = make(tmp_path, min_core=1)
    crawler.run(max_depth=1, max_artists=50)
    assert F not in queue(store)
    # Le gros label n'a coûté qu'une requête, et son payload stocké est tronqué
    assert label_calls(api).count(f"label:{BIG_LABEL}") == 1
    payload = store.get("musicbrainz", "label_releases", BIG_LABEL)
    assert payload["complete"] is False and payload["releases"] == []


def test_placeholders_are_ignored(tmp_path):
    crawler, store, api = make(tmp_path, min_core=1)
    crawler.run(max_depth=1, max_artists=50)
    assert f"label:{musicbrainz.NO_LABEL_MBID}" not in api.calls  # [no label] n'est pas un label
    assert VARIOUS not in queue(store)                             # Various Artists n'est pas un artiste


def test_zero_disables_expansion(tmp_path):
    crawler, store, api = make(tmp_path, label_max_releases=0)
    crawler.run(max_depth=1, max_artists=50)
    assert label_calls(api) == []
    assert E not in queue(store)


def test_no_expansion_at_max_depth(tmp_path):
    crawler, store, api = make(tmp_path)
    crawler.run(max_depth=0, max_artists=50)
    assert label_calls(api) == []
    assert set(queue(store)) == {A}


def test_shared_label_is_fetched_once_and_cached(tmp_path):
    crawler, store, api = make(tmp_path)
    crawler.run(max_depth=2, max_artists=50)
    assert label_calls(api).count(f"label:{LABEL}") == 1
    first = len(api.calls)
    store.con.execute("update raw.crawl_queue set status='pending' where status='done'")
    crawler.run(max_depth=2, max_artists=50)
    assert len(api.calls) == first


def test_existing_crawl_is_caught_up(tmp_path):
    """Une base crawlée sans expansion par labels voit ses labels explorés au run suivant,
    sans re-télécharger les artistes déjà traités."""
    crawler, store, api = make(tmp_path, label_max_releases=0)
    crawler.run(max_depth=1, max_artists=50)
    assert E not in queue(store)

    crawler.label_max_releases = 100
    before = len(api.calls)
    crawler.run(max_depth=1, max_artists=50)
    assert queue(store)[E][1] == "musicbrainz:label:Crypt Records"
    assert f"artist:{A}" not in api.calls[before:]  # A vient du cache


def test_raising_threshold_refetches_truncated_label(tmp_path):
    crawler, store, api = make(tmp_path, min_core=1)
    crawler.run(max_depth=1, max_artists=50)
    assert F not in queue(store)

    crawler.label_max_releases = 10_000  # le payload tronqué en cache ne suffit plus
    crawler.run(max_depth=1, max_artists=50)
    assert label_calls(api).count(f"label:{BIG_LABEL}") == 2
    assert F in queue(store)


# --- recalcul du périmètre ---------------------------------------------


def test_rescope_moves_old_label_expansion_out_of_scope(tmp_path):
    """Une file construite avec les anciennes règles (tout label <= 10 000 sorties, artistes de label
    étendus) est ramenée au périmètre actuel, sans rien supprimer ni télécharger."""
    crawler, store, api = make(tmp_path, label_max_releases=10_000, min_core=1)
    crawler.run(max_depth=1, max_artists=1)  # A seulement : B, C, E, F attendent
    store.enqueue(G, "Veil Choir", 2, E, "musicbrainz:label:Hollow Tapes")  # ancien comportement
    store.con.execute("update raw.crawl_queue set scope = null")             # file d'avant la migration
    crawler.run(max_depth=1, max_artists=50)

    crawler.label_max_releases, crawler.label_min_core_artists = 100, 2
    calls = len(api.calls)
    dry = crawler.rescope(max_depth=2)
    reasons = store.con.execute("select count(scope_reason) from raw.crawl_queue").fetchone()[0]
    assert reasons == 0  # simulation : rien d'écrit
    summary = crawler.rescope(max_depth=2, apply=True)
    assert len(api.calls) == calls     # aucune requête

    q = queue(store)
    assert {m: q[m][3] for m in (A, B, C)} == {A: "core", B: "core", C: "core"}
    assert q[E][3] == "label"                       # Crypt Records : partagé par A et B
    assert q[F][2:] == ("done", "out")              # déjà traité : reste done, marqué hors périmètre
    assert q[G][2:] == ("out_of_scope", "out")      # en attente : sorti de la file
    reason = store.con.execute("select scope_reason from raw.crawl_queue where mbid=?", [G]).fetchone()[0]
    assert reason == "label hors périmètre : Hollow Tapes"
    assert summary["pending_dropped"] == dry["pending_dropped"] >= 1
    assert "Pop Star" in summary["examples_out"]

    # La file ignore désormais G, et un run ne le remet pas en attente
    crawler.run(max_depth=2, max_artists=50)
    assert queue(store)[G][2] == "out_of_scope"


def test_rescope_is_reversible(tmp_path):
    """Exclure puis réinclure : F (seulement sur Mega Major) sort de la file puis y revient."""
    crawler, store, _ = make(tmp_path, label_max_releases=10_000, min_core=1)
    crawler.run(max_depth=1, max_artists=1)
    crawler.expand_core_labels(max_depth=1)
    assert queue(store)[F][2] == "pending"

    crawler.label_max_releases, crawler.label_min_core_artists = 100, 2
    crawler.rescope(max_depth=1, apply=True)
    assert queue(store)[F][2:] == ("out_of_scope", "out")

    crawler.label_max_releases, crawler.label_min_core_artists = 10_000, 1
    crawler.rescope(max_depth=1, apply=True)
    assert queue(store)[F][2:] == ("pending", "label")


def test_artist_labels_skip_no_label():
    from fake_api import ARTISTS
    labels = musicbrainz.artist_labels(ARTISTS[A], FakeApi().mb_releases(A))
    assert labels == {LABEL: "Crypt Records", BIG_LABEL: "Mega Major"}


def test_set_scopes_never_drops_crawled_artists(tmp_path):
    store = RawStore(tmp_path / "s.duckdb")
    for mbid, status in (("a", "done"), ("b", "pending"), ("c", "error")):
        store.enqueue(mbid, mbid, 1, None, "musicbrainz:label:X")
        store.mark(mbid, status)
    store.set_scopes([(m, "out", "test") for m in "abc"])
    rows = dict(store.con.execute("select mbid, status from raw.crawl_queue").fetchall())
    assert rows == {"a": "done", "b": "out_of_scope", "c": "out_of_scope"}
    assert store.requeue_errors() == 0  # out_of_scope n'est pas une erreur à réessayer
