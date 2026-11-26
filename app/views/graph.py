"""Page Graphe : un artiste, ses voisins (influences, membres, labels), et la source de chaque lien."""
from __future__ import annotations

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

import ui
from lineage.explore import db as xdb
from lineage.explore.graph import ALL_SOURCES, Neighborhood, artist_neighborhood


@st.cache_data(show_spinner=False)
def neighborhood(path: str, version: float, mbid: str, hops: int, sources: tuple, min_conf: float,
                 include_labels: bool, label_peers: int, include_out: bool) -> Neighborhood:
    return artist_neighborhood(path, mbid, hops, sources, min_conf, include_labels, label_peers, include_out)


def render(hood: Neighborhood, dark: bool) -> str:
    fg = "#E6E6E6" if dark else "#1F2328"
    net = Network(height="620px", width="100%", directed=True, bgcolor="rgba(0,0,0,0)", font_color=fg,
                  cdn_resources="remote")
    net.barnes_hut(gravity=-6000, spring_length=160)
    for n in hood.nodes.values():
        if n.kind == "label":
            net.add_node(n.id, label=n.label, shape="box", color="#B279A2", title=f"Label : {n.label}",
                         font={"color": "#FFFFFF", "size": 14})
        else:
            color = "#F58518" if n.is_center else ("#4C78A8" if n.in_scope else "#9D9D9D")
            net.add_node(n.id, label=n.label, color=color, size=28 if n.is_center else 16,
                         title=n.label + ("" if n.in_scope else "\nhors du périmètre crawlé"),
                         font={"color": fg, "size": 18 if n.is_center else 15})
    for e in hood.links:
        tooltip = [f"{hood.nodes[e.source].label} → {e.label} → {hood.nodes[e.target].label}",
                   f"Source : {ui.source_label(e.source_system, e.evidence_source)}"]
        if e.confidence is not None:
            tooltip.append(f"Confiance LLM : {e.confidence:.2f}")
        if e.evidence:
            tooltip.append(f"Citation : {e.evidence}")
        net.add_edge(e.source, e.target, title="\n".join(tooltip), label=e.label,
                     color="#B279A2" if e.edge_type == "on_label" else ui.SOURCE_COLORS[e.source_system],
                     dashes=e.source_system == "llm", font={"size": 11, "color": fg, "strokeWidth": 0, "align": "middle"},
                     arrows="to")
    # le gabarit pyvis pose une carte blanche : on la rend transparente pour suivre le thème Streamlit
    css = ("<style>html,body,.card,#mynetwork{background:transparent!important;border:none!important;"
           "margin:0;padding:0}</style>")
    return net.generate_html().replace("</head>", css + "</head>", 1)


st.title("Graphe de la scène")
mbids, labels = ui.artist_options()
if not mbids:
    st.info("Aucun artiste dans la base.")
    st.stop()

col_search, col_hops = st.columns([3, 1])
with col_search:
    mbid = st.selectbox("Artiste", mbids, format_func=labels.get, key="graph_artist",
                        help="Tape quelques lettres pour chercher.")
with col_hops:
    hops = st.radio("Distance", [1, 2], horizontal=True, format_func=lambda h: f"{h} saut{'s' * (h > 1)}")

with st.expander("Filtres"):
    c1, c2, c3 = st.columns(3)
    sources = c1.multiselect("Sources", ALL_SOURCES, default=list(ALL_SOURCES), format_func=ui.SOURCE_LABELS.get)
    min_conf = c2.slider("Confiance LLM minimale", 0.0, 1.0, 0.0, 0.05)
    include_labels = c3.toggle("Labels", value=True)
    label_peers = c3.slider("Autres artistes par label", 0, 30, 10, disabled=not include_labels)

hood = neighborhood(str(ui.db_path()), ui.db_version(), mbid, hops, tuple(sources), min_conf,
                    include_labels, label_peers, ui.show_out())
dark = st.context.theme.type == "dark"
components.html(render(hood, dark), height=640)
st.caption("Orange : l'artiste choisi · bleu : artistes crawlés · gris : hors périmètre · violet : labels. "
           "Traits pleins : MusicBrainz (bleu) et Wikidata (vert) ; pointillés rouges : extrait par le LLM. "
           "Survole un lien pour voir sa source et sa citation.")

st.subheader("Liens et sources")
names = {n.id: n.label for n in hood.nodes.values()}
table = pd.DataFrame([{
    "De": names[e.source], "Relation": e.label, "Vers": names[e.target],
    "Source": ui.source_label(e.source_system, e.evidence_source),
    "Citation / justification": e.evidence or "—",
    "Confiance": e.confidence,
} for e in hood.links])
if table.empty:
    st.info("Aucun lien avec ces filtres.")
else:
    st.dataframe(table, hide_index=True, width="stretch", column_config={
        "Confiance": st.column_config.NumberColumn(format="%.2f"),
        "Citation / justification": st.column_config.TextColumn(width="large"),
    })

details = xdb.artist_details(ui.db_path(), mbid)
if details and details["discogs_profile"]:
    with st.expander("Bio Discogs"):
        st.write(details["discogs_profile"])
