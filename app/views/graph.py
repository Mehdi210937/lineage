"""Page Graphe : un artiste, ses voisins les plus importants, et la source de chaque lien.

Lisibilité d'abord : un saut par défaut, labels masqués, filtres par type de lien, 15 voisins au plus
(« voir plus » pour la suite), noms courts, détails au survol, mise en page figée une fois stabilisée.
"""
from __future__ import annotations

import json

import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from pyvis.network import Network

import ui
from lineage.explore import db as xdb
from lineage.explore.graph import (
    ALL_SOURCES,
    CATEGORIES,
    DEFAULT_CATEGORIES,
    MAX_NEIGHBORS,
    Neighborhood,
    artist_neighborhood,
    drawn_edges,
    short_name,
)

NODE_COLORS = {"center": "#F58518", "artist": "#4C78A8", "out": "#9D9D9D", "label": "#B279A2"}
NODE_LEGEND = {"center": "artiste choisi", "artist": "artiste crawlé", "out": "hors périmètre", "label": "label"}
CATEGORY_COLORS = {"influences": "#E45756", "members": "#72B7B2", "remix": "#54A24B", "labels": "#B279A2"}

# Une fois la disposition stabilisée, on coupe la physique : les nœuds ne bougent plus que si on les tire.
FREEZE_JS = """
<script>
  (function () {
    var freeze = function () { network.setOptions({physics: false}); };
    network.once("stabilizationIterationsDone", freeze);
    setTimeout(freeze, 4000);  // filet de sécurité si l'évènement est déjà passé
  })();
</script>
"""
NET_OPTIONS = {
    "layout": {"randomSeed": 7},  # même graphe, même disposition
    "physics": {
        "solver": "forceAtlas2Based",
        "forceAtlas2Based": {"gravitationalConstant": -90, "centralGravity": 0.01, "springLength": 130,
                             "springConstant": 0.06, "damping": 0.6, "avoidOverlap": 0.8},
        "stabilization": {"enabled": True, "iterations": 500, "fit": True},
        "minVelocity": 1,
    },
    "interaction": {"hover": True, "tooltipDelay": 100},
    "edges": {"smooth": {"type": "continuous"}},
}


@st.cache_data(show_spinner=False)
def neighborhood(path: str, version: float, mbid: str, hops: int, sources: tuple, min_conf: float,
                 categories: tuple, limit: int, include_out: bool) -> Neighborhood:
    return artist_neighborhood(path, mbid, hops, sources, min_conf, categories, limit, include_out)


def node_kind(n) -> str:
    if n.kind == "label":
        return "label"
    return "center" if n.is_center else ("artist" if n.in_scope else "out")


def node_tooltip(n, n_links: int) -> str:
    lines = [n.label, NODE_LEGEND[node_kind(n)].capitalize()]
    if not n.is_center:
        lines.append(f"{n.hop} saut{'s' * (n.hop > 1)} de l'artiste choisi · {n_links} lien(s) affiché(s)")
    return "\n".join(lines)


def edge_tooltip(hood: Neighborhood, edge) -> str:
    parts = []
    for link in edge.links:
        lines = [f"{hood.nodes[link.source].label} → {link.label} → {hood.nodes[link.target].label}",
                 f"Source : {ui.source_label(link.source_system, link.evidence_source)}"]
        if link.confidence is not None:
            lines.append(f"Confiance LLM : {link.confidence:.2f}")
        if link.evidence:
            lines.append(f"« {link.evidence} »" if link.source_system == "llm" else link.evidence)
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


def render(hood: Neighborhood, dark: bool) -> str:
    fg = "#E6E6E6" if dark else "#1F2328"
    net = Network(height="620px", width="100%", directed=True, bgcolor="rgba(0,0,0,0)", font_color=fg,
                  cdn_resources="remote")
    net.set_options(json.dumps(NET_OPTIONS))
    degree = {n: 0 for n in hood.nodes}
    for link in hood.links:
        degree[link.source] += 1
        degree[link.target] += 1
    for n in hood.nodes.values():
        kind = node_kind(n)
        common = {"label": short_name(n.label), "title": node_tooltip(n, degree[n.id]), "color": NODE_COLORS[kind]}
        if kind == "label":
            net.add_node(n.id, shape="box", font={"color": "#FFFFFF", "size": 13}, **common)
        else:
            net.add_node(n.id, size=26 if n.is_center else (14 if n.hop == 1 else 10),
                         font={"color": fg, "size": 17 if n.is_center else (14 if n.hop == 1 else 12)}, **common)
    for edge in drawn_edges(hood):
        net.add_edge(edge.source, edge.target, title=edge_tooltip(hood, edge), arrows=edge.arrows,
                     color=CATEGORY_COLORS[edge.category], dashes=edge.llm_only,
                     width=2.5 if edge.category == "influences" else 1.5)
    # le gabarit pyvis pose une carte blanche : on la rend transparente pour suivre le thème Streamlit
    css = ("<style>html,body,.card,#mynetwork{background:transparent!important;border:none!important;"
           "margin:0;padding:0}</style>")
    html = net.generate_html().replace("</head>", css + "</head>", 1)
    head, tail = html.rsplit("</body>", 1)
    return head + FREEZE_JS + "</body>" + tail


def legend_html() -> str:
    dot = '<span style="display:inline-block;width:11px;height:11px;border-radius:50%;background:{}"></span>'
    box = '<span style="display:inline-block;width:14px;height:10px;border-radius:2px;background:{}"></span>'
    line = '<span style="display:inline-block;width:26px;border-top:3px {} {}"></span>'
    item = '<span style="margin-right:14px;white-space:nowrap">{} {}</span>'
    nodes = "".join(item.format((box if k == "label" else dot).format(c), NODE_LEGEND[k])
                    for k, c in NODE_COLORS.items())
    links = "".join(item.format(line.format("solid", CATEGORY_COLORS[k]), CATEGORIES[k]) for k in CATEGORIES)
    llm = item.format(line.format("dashed", "currentColor"), "extrait par le LLM (sinon MusicBrainz ou Wikidata)")
    return (f'<div style="font-size:0.85rem;line-height:1.9">'
            f"<b>Nœuds</b>&nbsp; {nodes}<br><b>Liens</b>&nbsp; {links}{llm}</div>")


def reset_limit() -> None:
    st.session_state["graph_limit"] = MAX_NEIGHBORS


st.title("Graphe de la scène")
mbids, labels = ui.artist_options()
if not mbids:
    st.info("Aucun artiste dans la base.")
    st.stop()
st.session_state.setdefault("graph_limit", MAX_NEIGHBORS)

col_search, col_hops = st.columns([3, 1], vertical_alignment="bottom")
with col_search:
    mbid = st.selectbox("Artiste", mbids, format_func=labels.get, key="graph_artist", on_change=reset_limit,
                        help="Tape quelques lettres pour chercher.")
with col_hops:
    hop2 = st.toggle("2e saut", key="graph_hop2", on_change=reset_limit,
                     help="Ajoute les voisins des voisins (graphe plus chargé).")

cat_cols = st.columns(len(CATEGORIES))
categories = tuple(
    k for k, col in zip(CATEGORIES, cat_cols)
    if col.checkbox(CATEGORIES[k], value=k in DEFAULT_CATEGORIES, key=f"graph_cat_{k}", on_change=reset_limit)
)

with st.expander("Sources et confiance"):
    c1, c2 = st.columns(2)
    sources = c1.multiselect("Sources", ALL_SOURCES, default=list(ALL_SOURCES), format_func=ui.SOURCE_LABELS.get,
                             on_change=reset_limit)
    min_conf = c2.slider("Confiance LLM minimale", 0.0, 1.0, 0.0, 0.05, on_change=reset_limit)

limit = st.session_state["graph_limit"]
hood = neighborhood(str(ui.db_path()), ui.db_version(), mbid, 2 if hop2 else 1, tuple(sources), min_conf,
                    categories, limit, ui.show_out())
dark = st.context.theme.type == "dark"
components.html(render(hood, dark), height=640)
st.html(legend_html())

shown = len(hood.nodes) - 1
info, more = st.columns([3, 1], vertical_alignment="center")
info.caption(
    f"{shown} voisin(s) affiché(s), au plus {limit} artistes et {limit} labels par saut, les plus importants "
    "d'abord : influence = 3 points, membre ou remix = 2, label = 1 ; à égalité, les artistes crawlés puis les "
    "plus reliés. Survole un nœud ou un lien pour les détails et la citation ; tire un nœud pour le déplacer."
)
if hood.hidden and more.button(f"Voir plus ({hood.hidden} masqué{'s' * (hood.hidden > 1)})", key="graph_more"):
    st.session_state["graph_limit"] = limit + MAX_NEIGHBORS
    st.rerun()

st.subheader("Liens et sources")
names = {n.id: n.label for n in hood.nodes.values()}
table = pd.DataFrame([{
    "De": names[e.source], "Relation": e.label, "Vers": names[e.target],
    "Type": CATEGORIES[e.category],
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
