"""Logique de l'app (graphe, labels, coach) sur la base de démo passée par dbt."""
from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from fake_api import A, B, BIG_LABEL, C, D, E, F, G, HOLLOW, LABEL

from lineage.explore import coach
from lineage.explore.db import resolve_db_path
from lineage.explore.graph import CATEGORIES, artist_neighborhood, drawn_edges, edge_category, short_name
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
    hood = artist_neighborhood(marts_db, A, hops=1, categories=tuple(CATEGORIES))
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
    assert len(llm) == 3  # Old Drone Master (bio + Wikipédia, fusionnés), Crypt Sister, Far Away Band
    assert all(l.evidence and l.confidence is not None for l in llm)
    assert {l.evidence_source for l in llm} <= {"discogs", "wikipedia"}
    to_c = next(l for l in llm if l.target == C)
    assert to_c.evidence_source == "wikipedia" and "main influence" in to_c.evidence
    far_away = next(l for l in llm if l.source == D)  # « influenced » est retourné : D influencé par A
    assert (far_away.target, far_away.edge_type) == (A, "influenced_by")


def test_neighborhood_filters(marts_db):
    only_mb = artist_neighborhood(marts_db, A, sources=("musicbrainz",))
    assert {l.source_system for l in only_mb.links} == {"musicbrainz"}
    assert LABEL not in only_mb.nodes  # labels masqués par défaut

    confident = artist_neighborhood(marts_db, A, sources=("llm",), min_llm_confidence=0.75)
    assert {(l.source, l.target) for l in confident.links} == {(A, C)}  # 0.9 ; les autres sont à 0.6


def test_two_hops_and_label_peers(marts_db):
    one = artist_neighborhood(marts_db, C, hops=1)
    two = artist_neighborhood(marts_db, C, hops=2)
    assert set(one.nodes) == {C, A}
    assert {B, D, E} <= set(two.nodes)

    labels_only = artist_neighborhood(marts_db, E, hops=1, sources=(), categories=("labels",))
    assert set(labels_only.nodes) == {E, LABEL, HOLLOW}  # un saut : ses labels, pas leurs artistes
    peers = artist_neighborhood(marts_db, E, hops=2, sources=(), categories=("labels",))
    assert {LABEL, HOLLOW, A, B, D} <= set(peers.nodes)  # deuxième saut : les artistes de ses labels
    assert {peers.nodes[n].hop for n in (A, B, D)} == {2}
    assert G not in peers.nodes  # E vient d'un label : Hollow Tapes n'a pas été exploré


def test_out_of_perimeter_artists_are_hidden_by_default(marts_db):
    """Pop Star (F) a été crawlé par le gros label avec d'anciennes règles : il reste en base, masqué."""
    from lineage.explore.db import list_artists, query

    assert query(marts_db, "select perimeter from marts.dim_artists where artist_mbid = ?", [F]) == [{"perimeter": "out"}]
    assert F not in {a["artist_mbid"] for a in list_artists(marts_db)}
    assert F in {a["artist_mbid"] for a in list_artists(marts_db, include_out=True)}

    every = tuple(CATEGORIES)
    hidden = artist_neighborhood(marts_db, A, hops=2, categories=every)
    shown = artist_neighborhood(marts_db, A, hops=2, categories=every, include_out=True)
    assert BIG_LABEL in hidden.nodes and F not in hidden.nodes
    assert F in shown.nodes

    mega = lambda rows: next(r for r in rows if r["label_name"] == "Mega Major")  # noqa: E731
    assert mega(rank_labels(marts_db, [A], current_year=2026, include_out=True))["n_artists_in_scope"] == 2
    assert "Pop Star" not in str(coach.build_facts(marts_db, [A]))


def test_category_filters(marts_db):
    members = artist_neighborhood(marts_db, A, categories=("members",))
    assert members.links and {l.category for l in members.links} == {"members"}
    assert C not in members.nodes  # Old Drone Master n'est relié à A que par des influences
    influences = artist_neighborhood(marts_db, A, categories=("influences",))
    assert C in influences.nodes and {l.category for l in influences.links} == {"influences"}
    assert edge_category("member of band") == edge_category("sibling") == "members"
    assert edge_category("sampled") == "remix" and edge_category("on_label") == "labels"


def test_limit_keeps_the_most_important_neighbors(marts_db):
    everything = artist_neighborhood(marts_db, A, limit=None)
    top = artist_neighborhood(marts_db, A, limit=1)
    n_artists = sum(n.kind == "artist" for n in everything.nodes.values()) - 1
    assert set(top.nodes) == {A, C}  # influence affirmée par Wikidata et par le LLM : 2 × 3 points
    assert top.hidden == n_artists - 1 > 0
    assert everything.hidden == 0


def test_links_of_a_pair_are_drawn_as_one_edge(marts_db):
    hood = artist_neighborhood(marts_db, A)
    edges = {frozenset((e.source, e.target)): e for e in drawn_edges(hood)}
    to_c = edges[frozenset((A, C))]
    assert {l.source_system for l in to_c.links} == {"wikidata", "llm"}
    assert to_c.category == "influences" and not to_c.llm_only and to_c.arrows == "to"
    assert len(edges) == len({frozenset((l.source, l.target)) for l in hood.links})


def test_short_name():
    assert short_name("oOoOO") == "oOoOO"
    assert short_name("Orchestral Manoeuvres in the Dark") == "Orchestral Mano…"
    assert len(short_name("Orchestral Manoeuvres in the Dark")) == 16


# --- labels -----------------------------------------------------------------


def test_rank_labels_explains_shared_artists(marts_db):
    ranked = {r["label_name"]: r for r in rank_labels(marts_db, [A], current_year=2026)}
    assert set(ranked) == {"Crypt Records", "Mega Major", "Hollow Tapes"}
    assert ranked["Crypt Records"]["shared_references"] == ["Test Coven"]
    assert ranked["Crypt Records"]["shared_neighbors"] == ["Crypt Sister", "Ghost Ritual"]
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
