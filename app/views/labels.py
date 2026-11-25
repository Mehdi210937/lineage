"""Page Labels : à partir de 1 à 3 artistes de référence, quels labels viser pour envoyer sa démo."""
from __future__ import annotations

import pandas as pd
import streamlit as st

import ui
from lineage.explore.labels import RECENCY_WINDOW, Weights, rank_labels


@st.cache_data(show_spinner=False)
def ranking(path: str, version: float, refs: tuple, weights: Weights, prefer_small: bool) -> list[dict]:
    return rank_labels(path, list(refs), weights, prefer_small)


st.title("Labels pour ma démo")
st.write("Choisis 1 à 3 artistes dont ta musique est proche : les labels sont classés selon les "
         "artistes qu'ils partagent avec eux, leur activité récente et leur taille.")

mbids, labels = ui.artist_options()
refs = st.multiselect("Artistes de référence", mbids, format_func=labels.get, max_selections=3,
                      key="label_refs", placeholder="Cherche un artiste…")

with st.expander("Pondération", expanded=False):
    c1, c2, c3 = st.columns(3)
    w_prox = c1.slider("Proximité (artistes partagés)", 0.0, 1.0, 0.5, 0.05)
    w_rec = c2.slider("Activité récente", 0.0, 1.0, 0.3, 0.05)
    w_size = c3.slider("Taille", 0.0, 1.0, 0.2, 0.05)
    prefer_small = st.radio(
        "Taille préférée", [True, False], horizontal=True,
        format_func=lambda s: "Petits labels (plus accessibles)" if s else "Gros labels (plus de visibilité)",
    )
    st.caption(
        f"Proximité : un artiste de référence sur le label compte 2, un de ses voisins dans le graphe compte 1. "
        f"Activité : 1 si le label a sorti un disque cette année, 0 s'il n'a rien sorti depuis {RECENCY_WINDOW} ans. "
        "Taille : nombre total de sorties dans MusicBrainz quand le crawler a exploré le label, sinon les "
        "sorties vues dans le périmètre crawlé."
    )

if not refs:
    st.info("Choisis au moins un artiste de référence.")
    st.stop()

rows = ranking(str(ui.db_path()), ui.db_version(), tuple(refs), Weights(w_prox, w_rec, w_size), prefer_small)
if not rows:
    st.warning("Aucun label relié à ces artistes ni à leurs voisins dans la base.")
    st.stop()

df = pd.DataFrame([{
    "Label": r["label_name"],
    "Score": r["score"],
    "Références sur le label": ", ".join(r["shared_references"]) or "—",
    "Voisins sur le label": ", ".join(r["shared_neighbors"]) or "—",
    "Proximité": r["proximity"],
    "Activité": r["recency"],
    "Taille (score)": r["size_score"],
    "Dernière sortie": r["last_release_year"],
    "Sorties (total)": r["size"],
    "Artistes crawlés": r["n_artists_in_scope"],
} for r in rows])

progress = {"min_value": 0.0, "max_value": 1.0, "format": "%.2f"}
st.dataframe(df, hide_index=True, width="stretch", column_config={
    "Score": st.column_config.ProgressColumn(**progress),
    "Proximité": st.column_config.ProgressColumn(**progress),
    "Activité": st.column_config.ProgressColumn(**progress),
    "Taille (score)": st.column_config.ProgressColumn(**progress),
    "Dernière sortie": st.column_config.NumberColumn(format="%d"),
})
st.caption("Les tailles ne couvrent que ce que le crawl a vu : un label absent ici n'est pas forcément "
           "hors sujet, il n'a peut-être pas été exploré.")
