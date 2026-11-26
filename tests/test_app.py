"""Tests de fumée de l'app Streamlit : chaque page s'affiche sans erreur sur la base de démo."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("streamlit")
pytest.importorskip("pyvis")

from fake_api import A  # noqa: E402
from streamlit.testing.v1 import AppTest  # noqa: E402

MAIN = str(Path(__file__).parents[1] / "app" / "main.py")


@pytest.fixture
def app(marts_db, monkeypatch):
    monkeypatch.setenv("LINEAGE_APP_DB", str(marts_db))
    monkeypatch.setenv("OLLAMA_URL", "http://127.0.0.1:9")  # Ollama injoignable : la page doit le dire
    return AppTest.from_file(MAIN, default_timeout=60)


def test_graph_page(app):
    app.run()
    assert not app.exception
    assert app.title[0].value == "Graphe de la scène"
    assert app.selectbox(key="graph_artist").value == A  # la seed est proposée en premier
    assert len(app.dataframe) == 1


def test_labels_page(app):
    app.switch_page("views/labels.py").run()
    assert not app.exception
    app.multiselect(key="label_refs").select(A).run()
    assert not app.exception
    ranked = app.dataframe[0].value
    assert set(ranked["Label"]) == {"Crypt Records", "Mega Major", "Hollow Tapes"}


def test_coach_page_without_ollama(app):
    app.switch_page("views/coach.py").run()
    assert not app.exception
    assert any("ne répond pas" in w.value for w in app.warning)
    app.multiselect(key="coach_artists").select(A).run()
    assert not app.exception
    assert app.button[0].disabled  # pas de LLM, pas de bouton


def test_missing_db_shows_help(monkeypatch, tmp_path):
    monkeypatch.setenv("LINEAGE_APP_DB", str(tmp_path / "absente.duckdb"))
    at = AppTest.from_file(MAIN, default_timeout=30).run()
    assert not at.exception
    assert "make_demo_db" in at.error[0].value


def test_out_of_perimeter_toggle(app):
    app.run()
    options = lambda: app.selectbox(key="graph_artist").options  # noqa: E731
    assert not any("Pop Star" in o for o in options())
    app.toggle(key="show_out").set_value(True).run()
    assert not app.exception
    assert any("Pop Star (GB, hors périmètre)" == o for o in options())
