"""Lineage : explorer le graphe de la scène, trouver un label, se faire coacher.

    streamlit run app/main.py                              # base réelle (data/lineage.duckdb)
    streamlit run app/main.py -- --db data/demo.duckdb     # base de démo

L'app lit uniquement les marts dbt, en lecture seule.
"""
from __future__ import annotations

import duckdb
import streamlit as st

import ui
from lineage.explore.db import query

st.set_page_config(page_title="Lineage", page_icon=":material/hub:", layout="wide")

path = ui.db_path()
with st.sidebar:
    st.caption(f"Base : `{path}`")


def check_db() -> str | None:
    """Message d'erreur si la base n'est pas utilisable, sinon None."""
    if not path.exists():
        return (f"La base `{path}` n'existe pas. Pour essayer l'app sur des données fictives :\n\n"
                "```\npython tests\\make_demo_db.py data\\demo.duckdb\n"
                "cd transform; dbt build --profiles-dir . --target demo; cd ..\n"
                "streamlit run app/main.py -- --db data/demo.duckdb\n```")
    try:
        query(path, "select 1 from marts.dim_artists limit 1")
    except duckdb.CatalogException:
        return f"Les marts n'existent pas dans `{path}` : lance d'abord `dbt build` dans `transform/`."
    except duckdb.Error as exc:
        # typiquement le verrou posé par un crawl ou un dbt build en cours sur ce fichier
        return (f"Impossible d'ouvrir `{path}` en lecture : {exc}\n\n"
                "Si un crawl ou un `dbt build` tourne sur cette base, relance l'app quand il a fini, "
                "ou utilise la démo : `streamlit run app/main.py -- --db data/demo.duckdb`")
    return None


error = check_db()
if error:
    st.title("Lineage")
    st.error(error)
    st.stop()

with st.sidebar:
    st.toggle("Afficher les artistes hors périmètre", key="show_out",
              help="Artistes crawlés avec d'anciennes règles, hors de la scène (voir --rescope).")

nav = st.navigation([
    st.Page("views/graph.py", title="Graphe", icon=":material/hub:", default=True),
    st.Page("views/labels.py", title="Labels pour ma démo", icon=":material/album:"),
    st.Page("views/coach.py", title="Coach", icon=":material/tune:"),
])
nav.run()
