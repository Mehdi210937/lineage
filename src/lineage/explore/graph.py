"""Voisinage d'un artiste dans le graphe : artistes reliés (influences, membres, remix…) et labels,
chaque lien portant sa source et sa justification.

Pour rester lisible, le voisinage est filtré par catégorie de lien et limité aux `limit` voisins
les plus importants à chaque saut ; les autres sont comptés dans `Neighborhood.hidden`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lineage.explore.db import EDGE_IN_PERIMETER, query

ALL_SOURCES = ("musicbrainz", "wikidata", "llm")

# Libellés lisibles des types d'arêtes (MusicBrainz, Wikidata, LLM)
EDGE_LABELS = {
    "influenced_by": "influencé par",
    "compared_to": "comparé à",
    "collaborated_with": "a collaboré avec",
    "sampled": "a samplé",
    "member of band": "membre de",
    "collaboration": "collaboration avec",
    "is person": "alias de",
    "subgroup": "sous-groupe de",
    "supporting musician": "musicien d'appoint de",
    "instrumental supporting musician": "musicien d'appoint de",
    "vocal supporting musician": "voix d'appoint de",
    "involved with": "en couple avec",
    "sibling": "frère ou sœur de",
    "parent": "parent de",
    "teacher": "professeur ou élève de",
    "tribute": "hommage à",
    "on_label": "sort sur",
}

# Catégories de liens proposées comme filtres, dans l'ordre d'affichage
CATEGORIES = {
    "influences": "Influences",
    "members": "Membres et proches",
    "remix": "Remix, split, collab",
    "labels": "Labels",
}
DEFAULT_CATEGORIES = ("influences", "members", "remix")  # labels masqués par défaut
EDGE_CATEGORY = {
    "influenced_by": "influences",
    "compared_to": "influences",
    "teacher": "influences",
    "tribute": "influences",
    "collaborated_with": "remix",
    "sampled": "remix",
    "collaboration": "remix",
    "on_label": "labels",
}
# Poids d'un lien dans l'importance d'un voisin : les influences sont la raison d'être du graphe
CATEGORY_WEIGHT = {"influences": 3, "remix": 2, "members": 2, "labels": 1}
MAX_NEIGHBORS = 15


def edge_category(edge_type: str) -> str:
    """Catégorie d'un type d'arête ; tout ce qui n'est ni influence, ni remix, ni label est un lien
    d'identité ou de proximité (membre, alias, sous-groupe, famille…)."""
    return EDGE_CATEGORY.get(edge_type, "members")


def short_name(name: str, max_len: int = 16) -> str:
    """Nom court pour l'étiquette d'un nœud ; le nom complet reste dans l'infobulle."""
    return name if len(name) <= max_len else name[: max_len - 1].rstrip() + "…"


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    kind: str              # artist | label
    is_center: bool = False
    in_scope: bool = True  # artiste crawlé (sinon : cité par une relation, hors périmètre)
    hop: int = 0           # distance à l'artiste choisi


@dataclass(frozen=True)
class Link:
    source: str
    target: str
    edge_type: str
    source_system: str     # musicbrainz | wikidata | llm
    evidence: str | None
    confidence: float | None = None
    evidence_source: str | None = None  # liens llm : discogs | wikipedia

    @property
    def label(self) -> str:
        return EDGE_LABELS.get(self.edge_type, self.edge_type)

    @property
    def category(self) -> str:
        return edge_category(self.edge_type)

    @property
    def key(self) -> tuple:
        return self.source, self.target, self.edge_type, self.source_system


@dataclass
class Neighborhood:
    nodes: dict[str, Node] = field(default_factory=dict)
    links: list[Link] = field(default_factory=list)
    hidden: int = 0        # voisins écartés par la limite (« voir plus »)

    def add_node(self, node: Node) -> None:
        # le premier ajout gagne : le centre, ajouté en premier, garde son statut
        if node.id not in self.nodes:
            self.nodes[node.id] = node

    def add_link(self, link: Link) -> None:
        if all(l.key != link.key for l in self.links):
            self.links.append(link)


@dataclass(frozen=True)
class DrawnEdge:
    """Un trait du dessin : tous les liens d'une même paire de nœuds, fusionnés (sources multiples,
    relations multiples), pour éviter les traits superposés."""
    source: str
    target: str
    links: tuple[Link, ...]

    @property
    def category(self) -> str:
        order = list(CATEGORIES)
        return min((l.category for l in self.links), key=lambda c: (-CATEGORY_WEIGHT[c], order.index(c)))

    @property
    def llm_only(self) -> bool:
        return all(l.source_system == "llm" for l in self.links)

    @property
    def arrows(self) -> str:
        both = any(l.source != self.source for l in self.links)
        return "to, from" if both else "to"


def drawn_edges(hood: Neighborhood) -> list[DrawnEdge]:
    groups: dict[frozenset, list[Link]] = {}
    for link in hood.links:
        groups.setdefault(frozenset((link.source, link.target)), []).append(link)
    return [DrawnEdge(ls[0].source, ls[0].target, tuple(ls)) for ls in groups.values()]


@dataclass
class _Candidate:
    node: Node
    links: list[Link] = field(default_factory=list)
    degree: int = 0        # nombre de liens dans tout le graphe (ou de sorties, pour un label)

    @property
    def importance(self) -> int:
        return sum(CATEGORY_WEIGHT[l.category] for l in self.links)

    def sort_key(self) -> tuple:
        return -self.importance, not self.node.in_scope, -self.degree, self.node.label.lower()


def artist_neighborhood(
    db_path: Path | str,
    mbid: str,
    hops: int = 1,
    sources: tuple[str, ...] = ALL_SOURCES,
    min_llm_confidence: float = 0.0,
    categories: tuple[str, ...] = DEFAULT_CATEGORIES,
    limit: int | None = MAX_NEIGHBORS,
    include_out: bool = False,
) -> Neighborhood:
    """Voisins de `mbid` jusqu'à `hops` sauts, pour les catégories de liens demandées.

    À chaque saut, les candidats sont classés par importance (somme des poids de leurs liens avec
    les nœuds déjà affichés, puis artistes crawlés d'abord, puis nombre de liens dans tout le graphe)
    et seuls les `limit` premiers artistes (et `limit` premiers labels) sont gardés. Les labels ne s'étendent qu'au saut suivant, vers les
    autres artistes du label. Les artistes hors périmètre sont masqués sauf si include_out.
    """
    hood = Neighborhood()
    center = query(db_path, "select artist_name from marts.dim_artists where artist_mbid = ?", [mbid])
    hood.add_node(Node(mbid, center[0]["artist_name"] if center else mbid[:8], "artist", is_center=True))
    degrees = _degrees(db_path)

    frontier = {mbid}
    for hop in range(1, hops + 1):
        candidates: dict[str, _Candidate] = {}
        artist_frontier = [n for n in frontier if hood.nodes[n].kind == "artist"]
        label_frontier = [n for n in frontier if hood.nodes[n].kind == "label"]
        _artist_candidates(db_path, hood, candidates, artist_frontier, hop, sources, min_llm_confidence,
                           categories, include_out, degrees)
        if "labels" in categories:
            if hop == 1:
                _label_candidates(db_path, candidates, mbid)
            _label_peer_candidates(db_path, hood, candidates, label_frontier, hop, include_out, degrees)

        # artistes et labels ont chacun leur quota : cocher « Labels » doit en montrer
        kept = []
        for kind in ("artist", "label"):
            ranked = sorted((c for c in candidates.values() if c.node.kind == kind), key=_Candidate.sort_key)
            kept += ranked if limit is None else ranked[:limit]
            hood.hidden += len(ranked) - len(ranked if limit is None else ranked[:limit])
        for c in kept:
            hood.add_node(c.node)
            for link in c.links:
                hood.add_link(link)
        frontier = {c.node.id for c in kept}
        if not frontier:
            break
    return hood


def _degrees(db_path: Path | str) -> dict[str, int]:
    rows = query(db_path, """
        select mbid, count(*) as n from (
            select from_artist_mbid as mbid from marts.edges_artists
            union all select to_artist_mbid from marts.edges_artists
        ) group by mbid""")
    return {r["mbid"]: r["n"] for r in rows}


def _artist_candidates(db_path, hood, candidates, frontier, hop, sources, min_conf, categories, include_out,
                       degrees) -> None:
    wanted = [c for c in categories if c != "labels"]
    if not frontier or not sources or not wanted:
        return
    rows = query(
        db_path,
        f"""
        select * from marts.edges_artists e
        where (from_artist_mbid in (select unnest(?)) or to_artist_mbid in (select unnest(?)))
          and source_system in (select unnest(?))
          and (source_system <> 'llm' or coalesce(confidence, 0) >= ?)
          and (? or {EDGE_IN_PERIMETER})
        order by source_system, edge_type, from_artist_mbid, to_artist_mbid
        """,
        [frontier, frontier, list(sources), min_conf, include_out],
    )
    for r in rows:
        if edge_category(r["edge_type"]) not in wanted:
            continue
        link = Link(r["from_artist_mbid"], r["to_artist_mbid"], r["edge_type"], r["source_system"],
                    r["evidence"], r["confidence"], r["evidence_source"])
        new = [s for s in ("from", "to") if r[f"{s}_artist_mbid"] not in hood.nodes]
        if not new:  # lien entre deux nœuds déjà affichés
            hood.add_link(link)
            continue
        for side in new:
            other, name = r[f"{side}_artist_mbid"], r[f"{side}_artist_name"]
            cand = candidates.setdefault(other, _Candidate(
                Node(other, name or f"{other[:8]}… (hors périmètre)", "artist", in_scope=name is not None, hop=hop),
                degree=degrees.get(other, 0)))
            if link not in cand.links:
                cand.links.append(link)


def _label_evidence(r: dict) -> str:
    parts = []
    if r["n_releases"]:
        years = sorted({y for y in (r["first_release_year"], r["last_release_year"]) if y})
        span = f" ({'–'.join(map(str, years))})" if years else ""
        parts.append(f"{r['n_releases']} sortie(s){span}")
    if r["relation_types"]:
        parts.append(f"relation : {r['relation_types']}")
    return "MusicBrainz : " + (", ".join(parts) or "lien artiste–label")


def _label_candidates(db_path, candidates, mbid) -> None:
    """Labels de l'artiste choisi ; à importance égale, ceux où il a le plus sorti d'abord."""
    labels = query(db_path, "select * from marts.bridge_artist_labels where artist_mbid = ?", [mbid])
    for lab in labels:
        candidates[lab["label_mbid"]] = _Candidate(
            Node(lab["label_mbid"], lab["label_name"], "label", hop=1),
            [Link(mbid, lab["label_mbid"], "on_label", "musicbrainz", _label_evidence(lab))],
            degree=lab["n_releases"] or 0,
        )


def _label_peer_candidates(db_path, hood, candidates, labels, hop, include_out, degrees) -> None:
    """Autres artistes des labels affichés (dans le périmètre, sauf include_out)."""
    if not labels:
        return
    peers = query(
        db_path,
        """
        select b.*, a.artist_name
        from marts.bridge_artist_labels b
        join marts.dim_artists a using (artist_mbid)
        where b.label_mbid in (select unnest(?)) and (a.in_perimeter or ?)
        """,
        [labels, include_out],
    )
    for p in peers:
        link = Link(p["artist_mbid"], p["label_mbid"], "on_label", "musicbrainz", _label_evidence(p))
        if p["artist_mbid"] in hood.nodes:
            hood.add_link(link)
            continue
        cand = candidates.setdefault(p["artist_mbid"], _Candidate(
            Node(p["artist_mbid"], p["artist_name"], "artist", hop=hop), degree=degrees.get(p["artist_mbid"], 0)))
        cand.links.append(link)
