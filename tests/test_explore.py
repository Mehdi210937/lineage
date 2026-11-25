"""Logique de l'app (graphe, labels, coach) sur la base de démo passée par dbt."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fake_api import A, B, BIG_LABEL, C, D, E, G, HOLLOW, LABEL

from lineage.explore import coach
from lineage.explore.db import resolve_db_path
from lineage.explore.graph import artist_neighborhood
from lineage.explore.labels import Weights, rank_labels

# --- base -------------------------------------------------------------------


def test_db_path_from_argv_then_env(monkeypatch):
    monkeypatch.setenv("LINEAGE_APP_DB", "env.duckdb")
    assert resolve_db_path(["--db", "data/demo.duckdb"]) == Path("data/demo.duckdb")
    assert resolve_db_path([]) == Path("env.duckdb")
    monkeypatch.delenv("LINEAGE_APP_DB")
    assert resolve_db_path(None) == Path("data/lineage.duckdb")


# --- graphe -----------------------------------------------------------------


def test_neighborhood_has_artists_labels_and_sources(marts_db):
    hood = artist_neighborhood(marts_db, A, hops=1, label_peers=0)
    assert hood.nodes[A].is_center
    assert {B, C, D, E, LABEL, BIG_LABEL} <= set(hood.nodes)
    assert hood.nodes[LABEL].kind == "label"
    systems = {(l.source, l.target, l.source_system) for l in hood.links}
    # la même influence, affirmée par deux sources, donne deux liens distincts
    assert {(A, C, "wikidata"), (A, C, "llm")} <= systems
    assert (B, A, "musicbrainz") in systems  # Ghost Ritual membre de Test Coven


def test_every_llm_link_carries_its_quote(marts_db):
    hood = artist_neighborhood(marts_db, A, hops=2)
    llm = [l for l in hood.links if l.source_system == "llm"]
    assert len(llm) == 4
    assert all(l.evidence and l.confidence is not None for l in llm)
    far_away = next(l for l in llm if l.source == D)  # « influenced » est retourné : D influencé par A
    assert (far_away.target, far_away.edge_type) == (A, "influenced_by")


def test_neighborhood_filters(marts_db):
    only_mb = artist_neighborhood(marts_db, A, sources=("musicbrainz",), include_labels=False)
    assert {l.source_system for l in only_mb.links} == {"musicbrainz"}
    assert LABEL not in only_mb.nodes

    confident = artist_neighborhood(marts_db, A, sources=("llm",), min_llm_confidence=0.75, include_labels=False)
    assert {l.edge_type for l in confident.links} == {"influenced_by", "collaborated_with"}


def test_two_hops_and_label_peers(marts_db):
    one = artist_neighborhood(marts_db, C, hops=1, include_labels=False)
    two = artist_neighborhood(marts_db, C, hops=2, include_labels=False)
    assert set(one.nodes) == {C, A}
    assert {B, D, E} <= set(two.nodes)

    peers = artist_neighborhood(marts_db, E, hops=1, sources=(), label_peers=10)
    assert {LABEL, HOLLOW, A, D, G} <= set(peers.nodes)  # artistes de ses labels


# --- labels -----------------------------------------------------------------


def test_rank_labels_explains_shared_artists(marts_db):
    ranked = {r["label_name"]: r for r in rank_labels(marts_db, [A], current_year=2026)}
    assert set(ranked) == {"Crypt Records", "Mega Major", "Hollow Tapes"}
    assert ranked["Crypt Records"]["shared_references"] == ["Test Coven"]
    assert ranked["Crypt Records"]["shared_neighbors"] == ["Crypt Sister"]
    assert ranked["Hollow Tapes"]["shared_neighbors"] == ["Crypt Sister", "Far Away Band"]
    assert ranked["Hollow Tapes"]["recency"] == 1.0
    assert ranked["Mega Major"]["size"] == 5000  # taille réelle, pas seulement le périmètre crawlé
    assert all(0 <= r["score"] <= 1 for r in ranked.values())


def test_size_preference_flips_order(marts_db):
    only_size = Weights(proximity=0, recency=0, size=1)
    small = rank_labels(marts_db, [A], only_size, prefer_small=True, current_year=2026)
    big = rank_labels(marts_db, [A], only_size, prefer_small=False, current_year=2026)
    assert small[-1]["label_name"] == "Mega Major"
    assert big[0]["label_name"] == "Mega Major"


def test_rank_labels_edge_cases(marts_db):
    assert rank_labels(marts_db, []) == []
    assert [r["label_name"] for r in rank_labels(marts_db, [G])] == ["Hollow Tapes"]
    assert rank_labels(marts_db, ["inconnu"]) == []


# --- coach ------------------------------------------------------------------


def test_facts_are_numbered_and_sourced(marts_db):
    facts = coach.build_facts(marts_db, [A, E])
    assert [f.id for f in facts] == [f"S{i}" for i in range(1, len(facts) + 1)]
    assert any(f.source == "Bio Discogs" and "chopped and screwed" in f.text for f in facts)
    llm = [f for f in facts if f.source.startswith("LLM")]
    assert llm and all(f.citation for f in llm)
    assert any("Mega Major" in f.text and "5000" in f.text for f in facts)
    assert {f.artist for f in facts} == {"Test Coven", "Crypt Sister"}


def test_messages_contain_every_fact_and_the_goal():
    facts = [coach.Fact("S1", "X", "MusicBrainz", "Genres de X : drone."),
             coach.Fact("S2", "X", "LLM", "X influencé par Y.", "très influencé par Y")]
    system, user = coach.build_messages(facts, ["X"], "morceau lent")
    assert "UNIQUEMENT" in system["content"]
    assert "[S1]" in user["content"] and "« très influencé par Y »" in user["content"]
    assert "morceau lent" in user["content"]


def test_validate_answer_rejects_unsourced_and_invented_sources():
    facts = [coach.Fact("S1", "X", "MusicBrainz", "a"), coach.Fact("S2", "X", "MusicBrainz", "b")]
    answer = coach.CoachAnswer(limites="", conseils=[
        coach.Advice(conseil="ok", sources=["S1", "[S2]", "S1"]),
        coach.Advice(conseil="sans source", sources=[]),
        coach.Advice(conseil="source inventée", sources=["S1", "S9"]),
    ])
    kept, rejected = coach.validate_answer(answer, facts)
    assert [(a.conseil, a.sources) for a in kept] == [("ok", ["S1", "S2"])]
    assert [a.conseil for a in rejected] == ["sans source", "source inventée"]


def test_ask_coach_constrains_output_to_schema():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        content = {"conseils": [{"conseil": "Noie la voix dans la réverb.", "sources": ["S1"]}], "limites": "tempo"}
        return httpx.Response(200, json={"message": {"content": json.dumps(content)}})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        answer = coach.ask_coach(client, "http://ollama", "qwen3:8b", [{"role": "user", "content": "x"}])
    assert answer.conseils[0].sources == ["S1"] and answer.limites == "tempo"
    assert seen["model"] == "qwen3:8b" and seen["think"] is False and seen["stream"] is False
    assert seen["format"] == coach.CoachAnswer.model_json_schema()


@pytest.mark.parametrize(("models", "ok"), [(["qwen3:8b"], True), (["llama3:latest"], False)])
def test_ollama_status(models, ok):
    def handler(request):
        return httpx.Response(200, json={"models": [{"name": m} for m in models]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        assert coach.ollama_status(client, "http://ollama", "qwen3:8b")[0] is ok


def test_ollama_down():
    def handler(request):
        raise httpx.ConnectError("refused")

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        ok, msg = coach.ollama_status(client, "http://ollama", "qwen3:8b")
    assert not ok and "ne répond pas" in msg
