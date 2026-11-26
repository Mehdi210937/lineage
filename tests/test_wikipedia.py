"""Source Wikipédia : sitelink enwiki, texte brut avec sections, intégration au crawl."""
from __future__ import annotations

import httpx
import pytest
from fake_api import A, TEST_COVEN_WIKIPEDIA, FakeApi

from lineage.crawl import Crawler
from lineage.http import PoliteClient
from lineage.sources import wikipedia
from lineage.store import RawStore


def client_answering(payload):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json=payload)

    return PoliteClient("lineage/test ( contact )", transport=httpx.MockTransport(handler), sleep=lambda s: None), seen


def test_sitelinks_gives_english_title():
    client, seen = client_answering(
        {"entities": {"Q1": {"sitelinks": {"enwiki": {"site": "enwiki", "title": "Salem (American band)"}}}}}
    )
    assert wikipedia.get_sitelinks(client, "Q1") == {"qid": "Q1", "enwiki": "Salem (American band)"}
    req = seen[0]
    assert req.url.host == "www.wikidata.org" and req.url.params["sitefilter"] == "enwiki"
    assert req.headers["User-Agent"] == "lineage/test ( contact )"  # politique User-Agent Wikimedia


def test_sitelinks_without_english_article():
    client, _ = client_answering({"entities": {"Q2": {"sitelinks": {}}}})
    assert wikipedia.get_sitelinks(client, "Q2") == {"qid": "Q2", "enwiki": None}
    with pytest.raises(ValueError):
        wikipedia.get_sitelinks(client, "Q2; drop")


def test_article_is_plain_text_with_sections():
    client, seen = client_answering(
        {"query": {"pages": [{"pageid": 7, "title": "Salem (American band)", "extract": "Intro.\n\n== Style ==\nDark."}]}}
    )
    art = wikipedia.get_article(client, "Salem (American band)")
    assert art == {"title": "Salem (American band)", "pageid": 7, "extract": "Intro.\n\n== Style ==\nDark."}
    params = seen[0].url.params
    assert params["explaintext"] == "1" and params["exsectionformat"] == "wiki" and params["redirects"] == "1"


def test_missing_article_is_none():
    client, _ = client_answering({"query": {"pages": [{"title": "Nope", "missing": True}]}})
    assert wikipedia.get_article(client, "Nope") is None


def test_split_sections():
    sections = wikipedia.split_sections(TEST_COVEN_WIKIPEDIA)
    assert [(t, lvl) for t, lvl, _ in sections] == [
        ("", 1), ("History", 2), ("Musical style and influences", 2), ("Discography", 2)
    ]
    assert sections[0][2] == "Test Coven is a fictional American witch house duo formed in 2008."
    assert sections[2][2].startswith("Test Coven cite Old Drone Master")
    assert wikipedia.split_sections("Juste une intro.") == [("", 1, "Juste une intro.")]


def test_crawl_stores_article_raw_and_caches_it(tmp_path):
    store = RawStore(tmp_path / "t.duckdb")
    api = FakeApi()
    crawler = Crawler(api, store)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=1, max_artists=50)

    article = store.get("wikipedia", "article", "Q900001")
    assert article["title"] == "Test Coven (band)" and "== Musical style and influences ==" in article["extract"]
    assert store.get("wikidata", "sitelinks", "Q900001") == {"qid": "Q900001", "enwiki": "Test Coven (band)"}
    assert api.calls.count("wikipedia:Test Coven (band)") == 1

    before = len(api.calls)
    crawler.run(max_depth=1, max_artists=50)  # rattrapage : tout est en cache
    assert len(api.calls) == before


def test_already_crawled_artists_get_their_article(tmp_path):
    """Base crawlée avant la source Wikipédia : le run suivant télécharge les articles manquants,
    sans re-télécharger les artistes ; un artiste hors périmètre est ignoré."""
    store = RawStore(tmp_path / "t.duckdb")
    api = FakeApi()
    crawler = Crawler(api, store)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=1, max_artists=50)
    store.con.execute("delete from raw.api_responses where source = 'wikipedia' or entity_type = 'sitelinks'")

    store.con.execute("update raw.crawl_queue set scope = 'out' where mbid = ?", [A])
    before = len(api.calls)
    crawler.run(max_depth=1, max_artists=50)
    assert not any(c.startswith(("sitelinks:", "wikipedia:")) for c in api.calls[before:])

    store.con.execute("update raw.crawl_queue set scope = 'core' where mbid = ?", [A])
    crawler.run(max_depth=1, max_artists=50)
    new = api.calls[before:]
    assert "wikipedia:Test Coven (band)" in new and f"artist:{A}" not in new
    assert store.get("wikipedia", "article", "Q900001") is not None


def test_wikipedia_error_does_not_stop_the_crawl(tmp_path):
    class Down(FakeApi):
        def wikipedia_article(self, title):
            raise httpx.ConnectError("en.wikipedia.org injoignable")

    store = RawStore(tmp_path / "t.duckdb")
    crawler = Crawler(Down(), store)
    crawler.resolve_seeds([{"name": "Test Coven", "note": None}])
    crawler.run(max_depth=1, max_artists=50)
    # l'article manque, mais l'artiste est traité et ses voisins ajoutés
    statuses = dict(store.con.execute("select mbid, status from raw.crawl_queue").fetchall())
    assert statuses[A] == "done" and len(statuses) > 1
    assert crawler.failures == 1 and store.get("wikipedia", "article", "Q900001") is None

    crawler.api = FakeApi()  # Wikipédia répond de nouveau : l'article est rattrapé
    crawler.run(max_depth=1, max_artists=50)
    assert store.get("wikipedia", "article", "Q900001") is not None
