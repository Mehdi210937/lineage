"""Extraction LLM : validation des citations, filtres, textes envoyés, cohérence avec dbt."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import duckdb
import httpx
from fake_api import A, B, DEMO_LLM_CLAIMS, TEST_COVEN_BIO_CLEAN, TEST_COVEN_WIKIPEDIA

from lineage.extract import run
from lineage.extract.schema import (
    ExtractionResult,
    InfluenceClaim,
    filter_claims,
    normalize_name,
    validate_against_source,
)
from lineage.extract.texts import wikipedia_passages

BIO = "Duo formé en 2008, très influencé par Old Drone Master et la scène chopped and screwed."


def claim(other, quote, relation="influenced_by", conf=0.9):
    return InfluenceClaim(other_artist=other, relation=relation, evidence_quote=quote, confidence=conf)


def test_keeps_quotes_present_in_source():
    r = ExtractionResult(claims=[claim("Old Drone Master", "très influencé par Old Drone Master")])
    assert len(validate_against_source(r, BIO).claims) == 1


def test_drops_hallucinated_or_empty_quotes():
    r = ExtractionResult(claims=[claim("Famous Band", "cite Famous Band comme influence majeure"),
                                 claim("Old Drone Master", "  ")])
    assert validate_against_source(r, BIO).claims == []


def test_filters_genres_places_labels_members_and_long_quotes():
    long_quote = "Their sound, " + "dense and slow, " * 15 + "is often compared to Salem."
    assert len(long_quote) > 200
    text = ("Former bassist of Kill Cheerleader, one half of Crystal Castles. A soul-influenced band, "
            "captivated by the Basque folk music, signed to Sub Pop Records. " + long_quote +
            " Influenced by Grimes.")
    r = ExtractionResult(claims=[
        claim("soul", "A soul-influenced band"),
        claim("Basque folk music", "captivated by the Basque folk music"),
        claim("Sub Pop Records", "signed to Sub Pop Records", "collaborated_with"),
        claim("Crystal Castles", "one half of Crystal Castles", "collaborated_with"),  # membre connu
        claim("Salem", long_quote, "compared_to"),
        claim("Ethan Kath", "Former bassist", "collaborated_with"),                   # l'artiste lui-même
        claim("Grimes", "Influenced by Grimes."),
        claim("grimes", "Influenced by Grimes."),                                     # doublon
        claim("Witch House", "A soul-influenced band"),                               # genre connu en base
    ])
    kept, rejected = filter_claims(r, text, artist_name="Ethan Kath", known_genres={"witch house"},
                                   known_identity_names={"crystal castles"})
    assert [c.other_artist for c in kept.claims] == ["Grimes"]
    assert rejected == {"genre, lieu ou label": 4, "membre ou alias déjà connu": 1, "citation trop longue": 1,
                        "l'artiste lui-même": 1, "doublon": 1}


def test_known_member_is_kept_when_cited_as_influence():
    """Le filtre « membre connu » ne vise que les collaborations : une influence reste une information."""
    text = "Heavily influenced by Crystal Castles."
    kept, _ = filter_claims(ExtractionResult(claims=[claim("Crystal Castles", "influenced by Crystal Castles")]),
                            text, known_identity_names={"crystal castles"})
    assert len(kept.claims) == 1


def test_normalize_name_matches_dbt_macro():
    # mêmes cas que le test unitaire dbt llm_name_resolution
    assert normalize_name("The Crÿpt-Sister") == normalize_name("crypt sister") == "crypt sister"
    assert normalize_name("Salem & Friends") == "salem and friends"
    assert normalize_name("///▲▲▲\\\\\\") == ""


def test_wikipedia_passages_put_style_first_then_intro():
    out = wikipedia_passages(TEST_COVEN_WIKIPEDIA)
    assert out.startswith("== Musical style and influences ==\nTest Coven cite Old Drone Master")
    assert out.endswith("Test Coven is a fictional American witch house duo formed in 2008.")
    assert "History" not in out and "Discography" not in out
    norm = lambda s: " ".join(s.split())  # noqa: E731
    for part in out.split("\n\n"):  # chaque passage est un morceau de l'article : citations vérifiables
        assert norm(part.split("\n", 1)[-1]) in norm(TEST_COVEN_WIKIPEDIA)


def test_wikipedia_passages_respect_budget():
    article = "Intro sentence. " * 50 + "\n\n== Style ==\n" + "Influenced by Someone. " * 500
    out = wikipedia_passages(article, max_chars=1000)
    assert len(out) <= 1000 and out.startswith("== Style ==") and out.endswith(".")
    assert wikipedia_passages("Only an intro.") == "Only an intro."


def test_demo_claims_quote_their_source_text():
    """Les affirmations fictives de la démo respectent les mêmes règles que le vrai extracteur."""
    texts = {"discogs": TEST_COVEN_BIO_CLEAN, "wikipedia": wikipedia_passages(TEST_COVEN_WIKIPEDIA)}
    for kind, body in texts.items():
        claims = [claim(o, q, r, c) for _, o, r, q, c, k in DEMO_LLM_CLAIMS if o and k == kind]
        kept, rejected = filter_claims(ExtractionResult(claims=claims), body, "Test Coven")
        assert len(kept.claims) == len(claims) and not rejected, (kind, rejected)


def test_extractor_version_matches_dbt_var():
    project = (Path(__file__).parents[1] / "transform" / "dbt_project.yml").read_text(encoding="utf-8")
    assert re.search(r'llm_extractor_version:\s*"(\w+)"', project).group(1) == run.EXTRACTOR_VERSION


def test_extract_sends_text_without_thinking():
    seen = {}

    def handler(request):
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": '{"claims": []}'}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = run.extract(client, "http://ollama", "qwen3:8b", "Test Coven", "Some text.", "wikipedia")
    assert result.claims == []
    assert seen["think"] is False and seen["format"] == ExtractionResult.model_json_schema()
    assert "Article Wikipédia :\nSome text." in seen["messages"][1]["content"]


def test_texts_to_process_and_known_context(marts_db, tmp_path):
    db = tmp_path / "copy.duckdb"
    shutil.copy(marts_db, db)
    con = duckdb.connect(str(db))
    todo = run.texts_to_process(con, "qwen3:8b", None)
    assert [(m, k) for m, _, k, _ in todo] == [(A, "wikipedia"), (A, "discogs")]  # seul A a des textes
    wiki = todo[0][3]
    assert wiki.startswith("== Musical style and influences ==") and "Discography" not in wiki
    assert todo[1][3] == TEST_COVEN_BIO_CLEAN  # bio sans balisage Discogs

    genres, identity = run.known_context(con)
    assert {"witch house", "dark", "pop"} <= genres
    assert identity[A] >= {"ghost ritual"} and identity[B] >= {"test coven"}

    con.execute("insert into raw.llm_influence_claims (artist_mbid, model, text_source, extractor_version) "
                "values (?, 'qwen3:8b', 'wikipedia', ?)", [A, run.EXTRACTOR_VERSION])
    assert [k for _, _, k, _ in run.texts_to_process(con, "qwen3:8b", None)] == ["discogs"]
    con.close()
