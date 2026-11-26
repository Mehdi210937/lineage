"""Voisinage d'un artiste dans le graphe : artistes reliés (influences, membres, collaborations…)
et labels, chaque lien portant sa source et sa justification."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from lineage.explore.db import EDGE_IN_PERIMETER, query

ALL_SOURCES = ("musicbrainz", "wikidata", "llm")

# Libellés lisibles des types d'arêtes (MusicBrainz + LLM)
EDGE_LABELS = {
    "influenced_by": "influencé par",
    "compared_to": "comparé à",
    "collaborated_with": "a collaboré avec",
    "sampled": "a samplé",
    "member of band": "membre de",
    "collaboration": "collaboration avec",
    "is person": "alias de",
    "on_label": "sort sur",
}


@dataclass(frozen=True)
class Node:
    id: str
    label: str
    kind: str              # artist | label
    is_center: bool = False
    in_scope: bool = True  # artiste crawlé (sinon : cité par une relation, hors périmètre)


@dataclass(frozen=True)
class Link:
    source: str
    target: str
    edge_type: str
    source_system: str     # musicbrainz | wikidata | llm
    evidence: str | None
    confidence: float | None = None

    @property
    def label(self) -> str:
        return EDGE_LABELS.get(self.edge_type, self.edge_type)


@dataclass
class Neighborhood:
    nodes: dict[str, Node] = field(default_factory=dict)
    links: list[Link] = field(default_factory=list)

    def add_node(self, node: Node) -> None:
        # le premier ajout gagne : le centre, ajouté en premier, garde son statut
        if node.id not in self.nodes:
            self.nodes[node.id] = node


def artist_neighborhood(
    db_path: Path | str,
    mbid: str,
    hops: int = 1,
    sources: tuple[str, ...] = ALL_SOURCES,
    min_llm_confidence: float = 0.0,
    include_labels: bool = True,
    label_peers: int = 10,
    include_out: bool = False,
) -> Neighborhood:
    """Artistes à `hops` sauts de `mbid`, ses labels, et jusqu'à `label_peers` autres artistes par label.
    Les artistes hors périmètre (dim_artists.in_perimeter) sont masqués sauf si include_out."""
    hood = Neighborhood()
    center = query(db_path, "select artist_name from marts.dim_artists where artist_mbid = ?", [mbid])
    hood.add_node(Node(mbid, center[0]["artist_name"] if center else mbid[:8], "artist", is_center=True))

    frontier, seen = {mbid}, {mbid}
    seen_links: set[tuple] = set()
    for _ in range(hops):
        if not frontier or not sources:
            break
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
            [list(frontier), list(frontier), list(sources), min_llm_confidence, include_out],
        )
        next_frontier = set()
        for r in rows:
            for side in ("from", "to"):
                other = r[f"{side}_artist_mbid"]
                name = r[f"{side}_artist_name"]
                hood.add_node(Node(other, name or f"{other[:8]}… (hors périmètre)", "artist",
                                   in_scope=name is not None))
                if other not in seen:
                    next_frontier.add(other)
            key = (r["from_artist_mbid"], r["to_artist_mbid"], r["edge_type"], r["source_system"])
            if key not in seen_links:
                seen_links.add(key)
                hood.links.append(Link(*key, r["evidence"], r["confidence"]))
        seen |= next_frontier
        frontier = next_frontier

    if include_labels:
        _add_labels(db_path, hood, mbid, label_peers, include_out)
    return hood


def _label_evidence(r: dict) -> str:
    parts = []
    if r["n_releases"]:
        years = sorted({y for y in (r["first_release_year"], r["last_release_year"]) if y})
        span = f" ({'–'.join(map(str, years))})" if years else ""
        parts.append(f"{r['n_releases']} sortie(s){span}")
    if r["relation_types"]:
        parts.append(f"relation : {r['relation_types']}")
    return "MusicBrainz : " + (", ".join(parts) or "lien artiste–label")


def _add_labels(db_path: Path | str, hood: Neighborhood, mbid: str, label_peers: int, include_out: bool) -> None:
    labels = query(
        db_path,
        "select * from marts.bridge_artist_labels where artist_mbid = ? order by n_releases desc, label_name",
        [mbid],
    )
    for lab in labels:
        hood.add_node(Node(lab["label_mbid"], lab["label_name"], "label"))
        hood.links.append(Link(mbid, lab["label_mbid"], "on_label", "musicbrainz", _label_evidence(lab)))
        if label_peers <= 0:
            continue
        peers = query(
            db_path,
            """
            select b.*, a.artist_name
            from marts.bridge_artist_labels b
            join marts.dim_artists a using (artist_mbid)
            where b.label_mbid = ? and b.artist_mbid <> ? and (a.in_perimeter or ?)
            order by b.last_release_year desc nulls last, a.artist_name
            limit ?
            """,
            [lab["label_mbid"], mbid, include_out, label_peers],
        )
        for p in peers:
            hood.add_node(Node(p["artist_mbid"], p["artist_name"], "artist"))
            hood.links.append(
                Link(p["artist_mbid"], lab["label_mbid"], "on_label", "musicbrainz", _label_evidence(p))
            )
