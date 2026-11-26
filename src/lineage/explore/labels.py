"""« À quel label envoyer ma démo ? » : classe les labels à partir de 1 à 3 artistes de référence.

Trois critères, chacun ramené entre 0 et 1 :
- proximité : artistes du label qui sont les références elles-mêmes (poids 2) ou leurs voisins
  directs dans le graphe (poids 1), rapporté au meilleur label ;
- activité récente : 1 si le label a sorti un disque cette année, 0 s'il n'a rien sorti depuis
  `RECENCY_WINDOW` ans ;
- taille : log du nombre total de sorties (MusicBrainz), inversé si on préfère les petits labels.
Seuls les labels qui ont au moins un artiste partagé sont classés.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from lineage.explore.db import EDGE_IN_PERIMETER, query

RECENCY_WINDOW = 10


@dataclass(frozen=True)
class Weights:
    proximity: float = 0.5
    recency: float = 0.3
    size: float = 0.2


def rank_labels(
    db_path: Path | str,
    reference_mbids: list[str],
    weights: Weights = Weights(),
    prefer_small: bool = True,
    current_year: int | None = None,
    include_out: bool = False,
) -> list[dict]:
    """Labels classés pour ces références ; les artistes hors périmètre ne comptent pas, sauf include_out."""
    if not reference_mbids:
        return []
    current_year = current_year or date.today().year
    refs = set(reference_mbids)

    neighbors = {
        r["other"]
        for r in query(
            db_path,
            f"""
            select case when from_artist_mbid in (select unnest(?)) then to_artist_mbid
                        else from_artist_mbid end as other
            from marts.edges_artists e
            where (from_artist_mbid in (select unnest(?)) or to_artist_mbid in (select unnest(?)))
              and (? or {EDGE_IN_PERIMETER})
            """,
            [list(refs)] * 3 + [include_out],
        )
    } - refs

    rows = query(
        db_path,
        """
        select l.label_mbid, l.label_name, l.n_artists_in_scope, l.n_releases_in_scope,
               l.n_releases_total, l.first_release_year, l.last_release_year,
               b.artist_mbid, a.artist_name
        from marts.dim_labels l
        join marts.bridge_artist_labels b using (label_mbid)
        left join marts.dim_artists a using (artist_mbid)
        where l.label_mbid in (
            select label_mbid from marts.bridge_artist_labels where artist_mbid in (select unnest(?))
        )
          and (coalesce(a.in_perimeter, true) or ?)
        """,
        [list(refs | neighbors), include_out],
    )

    labels: dict[str, dict] = {}
    for r in rows:
        lab = labels.setdefault(r["label_mbid"], {
            **{k: r[k] for k in ("label_mbid", "label_name", "n_artists_in_scope", "n_releases_in_scope",
                                  "n_releases_total", "first_release_year", "last_release_year")},
            "shared_references": [], "shared_neighbors": [],
        })
        name = r["artist_name"] or r["artist_mbid"][:8]
        if r["artist_mbid"] in refs:
            lab["shared_references"].append(name)
        elif r["artist_mbid"] in neighbors:
            lab["shared_neighbors"].append(name)

    if not labels:
        return []

    sizes = {k: (v["n_releases_total"] or v["n_releases_in_scope"] or 0) for k, v in labels.items()}
    max_log_size = max(math.log10(1 + s) for s in sizes.values()) or 1.0
    raw_prox = {k: 2 * len(v["shared_references"]) + len(v["shared_neighbors"]) for k, v in labels.items()}
    max_prox = max(raw_prox.values()) or 1
    total_w = (weights.proximity + weights.recency + weights.size) or 1.0

    for k, lab in labels.items():
        last = lab["last_release_year"]
        recency = 0.0 if last is None else max(0.0, 1 - (current_year - last) / RECENCY_WINDOW)
        size_big = math.log10(1 + sizes[k]) / max_log_size
        lab["proximity"] = raw_prox[k] / max_prox
        lab["recency"] = min(recency, 1.0)
        lab["size"] = sizes[k]
        lab["size_score"] = 1 - size_big if prefer_small else size_big
        lab["score"] = (weights.proximity * lab["proximity"] + weights.recency * lab["recency"]
                        + weights.size * lab["size_score"]) / total_w
        lab["shared_references"].sort()
        lab["shared_neighbors"].sort()

    return sorted(labels.values(), key=lambda v: (-v["score"], v["label_name"] or ""))
