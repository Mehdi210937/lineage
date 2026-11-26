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
    SYSTEM_PROMPT,
    ExtractionResult,
    InfluenceClaim,
    LLMClaim,
    LLMResult,
    filter_claims,
    is_not_an_artist,
    name_in_sentence,
    normalize_name,
    resolve_sentences,
    validate_against_source,
)
from lineage.extract.texts import (
    MAX_CHUNKS,
    chunk_blocks,
    numbered,
    split_sentences,
    text_chunks,
    wikipedia_blocks,
)

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


def test_filters_genres_places_labels_and_members():
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
        claim("Ethan Kath", "Former bassist", "collaborated_with"),                   # l'artiste lui-même
        claim("Grimes", "Influenced by Grimes."),
        claim("grimes", "Influenced by Grimes."),                                     # doublon
        claim("Witch House", "A soul-influenced band"),                               # genre connu en base
    ])
    kept, rejected = filter_claims(r, text, artist_name="Ethan Kath", known_genres={"witch house"},
                                   known_identity_names={"crystal castles"})
    assert [c.other_artist for c in kept.claims] == ["Grimes"]
    assert rejected == {"genre, lieu ou label": 4, "membre ou alias déjà connu": 1,
                        "l'artiste lui-même": 1, "doublon": 1}


def test_known_member_is_ignored_whatever_the_relation():
    """Vu sur Crystal Castles : « …featuring a new vocalist named Edith Frances » extrait comme
    influenced_by. Une chanteuse du groupe, déjà connue par MusicBrainz, n'est pas une influence."""
    text = "Ethan Kath released a new track featuring a new vocalist named Edith Frances."
    kept, rejected = filter_claims(
        ExtractionResult(claims=[claim("Edith Frances", text)]), text, known_identity_names={"edith frances"})
    assert kept.claims == [] and rejected == {"membre ou alias déjà connu": 1}


def test_shared_stage_is_not_a_collaboration():
    """Phrases réelles (Child Abuse, Crystal Castles) que qwen3:8b extrayait comme collaborations."""
    stage = [
        ("Ruins", "Child Abuse has shared bills with many bands including Suicide, Ruins and Liturgy."),
        ("Nine Inch Nails", "Crystal Castles played five dates with Nine Inch Nails in August 2008."),
        ("Blur", "They supported Blur on the first of two comeback shows in Hyde Park, London in July 2009."),
        ("HEALTH", "Crystal Castles toured North America in the fall of 2012 with old collaborators HEALTH."),
        ("Depeche Mode", "They also played the Big Day Out festival with Depeche Mode."),
    ]
    real = [("Robert Smith", 'The third single, "Not in Love" featuring Robert Smith from the Cure, charted.')]
    text = " ".join(q for _, q in stage + real)
    claims = [claim(o, q, "collaborated_with") for o, q in stage + real]
    claims.append(claim("Blur", stage[2][1], "compared_to"))  # le filtre ne vise que les collaborations
    kept, rejected = filter_claims(ExtractionResult(claims=claims), text)
    assert [(c.other_artist, c.relation) for c in kept.claims] == [("Robert Smith", "collaborated_with"),
                                                                   ("Blur", "compared_to")]
    assert rejected == {"affiche ou tournée partagée": 5}


def test_normalize_name_matches_dbt_macro():
    # mêmes cas que le test unitaire dbt llm_name_resolution
    assert normalize_name("The Crÿpt-Sister") == normalize_name("crypt sister") == "crypt sister"
    assert normalize_name("Salem & Friends") == "salem and friends"
    assert normalize_name("///▲▲▲\\\\\\") == ""


def test_wikipedia_reading_order_style_intro_then_rest():
    blocks = wikipedia_blocks(TEST_COVEN_WIKIPEDIA)
    assert blocks[0].startswith("== Musical style and influences ==\nTest Coven cite Old Drone Master")
    assert blocks[1] == "Test Coven is a fictional American witch house duo formed in 2008."
    assert blocks[2].startswith("== History ==")  # le reste de l'article est lu aussi
    assert not any("Discography" in b for b in blocks)


def test_article_without_style_section_is_read_entirely():
    """Le défaut constaté sur le vrai crawl : sans section « style », seule l'intro partait au LLM."""
    article = "Intro. " * 20 + "\n\n== History ==\n" + "They cited Suicide as an influence. " * 40 \
        + "\n\n== Discography ==\nAlbum (2010)"
    chunks = text_chunks("wikipedia", article)
    joined = "\n".join(chunks)
    assert "They cited Suicide" in joined and "Album (2010)" not in joined
    assert len(chunks) == 1 and chunks[0].startswith("Intro.")


def test_long_articles_are_split_into_bounded_chunks():
    article = "Intro sentence here. " * 30 + "\n\n== Style ==\n" + "Influenced by Someone Else. " * 400 \
        + "\n\n== Career ==\n" + "They toured a lot. " * 100
    chunks = chunk_blocks(wikipedia_blocks(article), max_chars=1000, max_chunks=50)
    assert all(len(c) <= 1000 for c in chunks)
    assert chunks[0].startswith("== Style ==")
    norm = lambda s: " ".join(s.split())  # noqa: E731
    for c in chunks:  # aucun texte réécrit : chaque phrase reste une sous-chaîne de l'article
        for sentence in split_sentences(c):
            assert norm(sentence) in norm(article)
    assert len(chunk_blocks(wikipedia_blocks(article), max_chars=1000)) == MAX_CHUNKS


def test_split_sentences_keeps_exact_substrings():
    text = "Formed by J. Mascis in 1984. They cited “Suicide.” Is it 2.0? Yes!\n== Style ==\nDark sound."
    assert split_sentences(text) == ["Formed by J. Mascis in 1984.", "They cited “Suicide.”", "Is it 2.0?", "Yes!",
                                     "Dark sound."]
    shown, sentences = numbered(text)
    assert shown.splitlines()[0] == "[1] Formed by J. Mascis in 1984."
    assert "== Style ==" in shown and "[5] Dark sound." in shown and len(sentences) == 5


def llm(*claims):
    return LLMResult(claims=[LLMClaim(other_artist=o, relation=r, sentence=n, confidence=0.9) for o, r, n in claims])


def test_sentence_numbers_become_original_sentences():
    """Le cas Child Abuse : une énumération justifie plusieurs artistes avec la même phrase originale,
    au lieu de phrases réécrites une par artiste."""
    _, sentences = numbered("Child Abuse formed in 2005. They have shared bills with Suicide, Ruins and Arab on "
                            "Radar. They cite Lightning Bolt as an influence.")
    found, rejected = resolve_sentences(llm(
        ("Lightning Bolt", "influenced_by", 3),
        ("Ruins", "compared_to", 2),
        ("Arab on Radar", "compared_to", 2),
        ("Suicide", "influenced_by", 3),       # mauvais numéro : Suicide n'est pas dans la phrase 3
        ("Lightning Bolt", "influenced_by", 9),  # hors du texte
    ), sentences)
    assert [(c.other_artist, c.evidence_quote) for c in found] == [
        ("Lightning Bolt", "They cite Lightning Bolt as an influence."),
        ("Ruins", "They have shared bills with Suicide, Ruins and Arab on Radar."),
        ("Arab on Radar", "They have shared bills with Suicide, Ruins and Arab on Radar."),
    ]
    assert rejected == {"artiste absent de la phrase": 1, "numéro de phrase invalide": 1}
    assert name_in_sentence("The Locust", "they toured with The Locust.") and not name_in_sentence("Ruin", "Ruins")


def test_prompt_forbids_shared_bills_and_works():
    assert "affiche" in SYSTEM_PROMPT and "jeu vidéo" in SYSTEM_PROMPT and "Ne recopie jamais" in SYSTEM_PROMPT
    assert is_not_an_artist("Rockstar Games") and is_not_an_artist("Universal Pictures")


def test_demo_claims_quote_their_source_text():
    """Les affirmations fictives de la démo respectent les mêmes règles que le vrai extracteur :
    leur citation est une phrase entière du texte, qui nomme l'artiste."""
    texts = {"discogs": TEST_COVEN_BIO_CLEAN, "wikipedia": TEST_COVEN_WIKIPEDIA}
    for kind, body in texts.items():
        sentences = split_sentences(body)
        demo = [(o, r, q, c) for _, o, r, q, c, k in DEMO_LLM_CLAIMS if o and k == kind]
        found, bad = resolve_sentences(
            LLMResult(claims=[LLMClaim(other_artist=o, relation=r, sentence=sentences.index(q) + 1, confidence=c)
                              for o, r, q, c in demo]), sentences)
        kept, rejected = filter_claims(ExtractionResult(claims=found), body, "Test Coven")
        assert len(kept.claims) == len(demo) and not bad and not rejected, (kind, bad, rejected)


def test_extractor_version_matches_dbt_var():
    project = (Path(__file__).parents[1] / "transform" / "dbt_project.yml").read_text(encoding="utf-8")
    assert re.search(r'llm_extractor_version:\s*"(\w+)"', project).group(1) == run.EXTRACTOR_VERSION


def test_extract_sends_numbered_sentences_without_thinking():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        answer = {"claims": [{"other_artist": "Suicide", "relation": "influenced_by", "sentence": 2,
                              "confidence": 0.9}]}
        return httpx.Response(200, json={"message": {"content": json.dumps(answer)}})

    chunks = ["Formed in 2005. They cite Suicide as a key influence.", "Second chunk. Nothing here."]
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result, rejected = run.extract(client, "http://ollama", "qwen3:8b", "Child Abuse", chunks, "wikipedia")
    assert len(seen) == 2  # un appel par morceau
    assert seen[0]["think"] is False and seen[0]["format"] == LLMResult.model_json_schema()
    assert "Article Wikipédia :\n[1] Formed in 2005.\n[2] They cite Suicide" in seen[0]["messages"][1]["content"]
    assert [c.evidence_quote for c in result.claims] == ["They cite Suicide as a key influence."]
    assert rejected == {"artiste absent de la phrase": 1}  # même réponse sur le 2e morceau : refusée


def test_texts_to_process_and_known_context(marts_db, tmp_path):
    db = tmp_path / "copy.duckdb"
    shutil.copy(marts_db, db)
    con = duckdb.connect(str(db))
    todo = run.texts_to_process(con, "qwen3:8b", None)
    assert [(m, k) for m, _, k, _ in todo] == [(A, "wikipedia"), (A, "discogs")]  # seul A a des textes
    wiki_chunks = todo[0][3]
    assert wiki_chunks[0].startswith("== Musical style and influences ==") and "History" in wiki_chunks[0]
    assert todo[1][3] == [TEST_COVEN_BIO_CLEAN]  # bio sans balisage Discogs

    genres, identity = run.known_context(con)
    assert {"witch house", "dark", "pop"} <= genres
    assert identity[A] >= {"ghost ritual"} and identity[B] >= {"test coven"}

    con.execute("insert into raw.llm_influence_claims (artist_mbid, model, text_source, extractor_version) "
                "values (?, 'qwen3:8b', 'wikipedia', ?)", [A, run.EXTRACTOR_VERSION])
    assert [k for _, _, k, _ in run.texts_to_process(con, "qwen3:8b", None)] == ["discogs"]
    con.close()
