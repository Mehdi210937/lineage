"""Crawl en largeur du graphe d'artistes à partir des artistes de départ.

Usage :
    python -m lineage.crawl --depth 2 --max-artists 400 [--label-max-releases 300]

Voisins d'un artiste : ses relations MusicBrainz (membres, alias...), ses influences Wikidata, et les
autres artistes de ses labels de niche (labels ayant au plus --label-max-releases sorties ; 0 = désactivé).

Le crawl est reprenable : la file est stockée dans DuckDB (raw.crawl_queue).
Si tu l'interromps (Ctrl+C), relance la même commande, il reprend où il en était.
Une erreur sur un artiste ou un label (API saturée, 503…) n'arrête jamais le crawl : elle est
journalisée, l'élément est marqué pour être réessayé au prochain run, et le crawl continue.
Les réponses déjà en cache depuis moins de LINEAGE_REFRESH_DAYS jours ne sont pas re-téléchargées.
"""
from __future__ import annotations

import argparse
import csv
import logging
from pathlib import Path
from typing import Protocol

from lineage.config import Settings, get_settings
from lineage.http import PoliteClient
from lineage.sources import discogs, musicbrainz, wikidata
from lineage.store import RawStore

log = logging.getLogger("lineage.crawl")


class Api(Protocol):
    def mb_search(self, name: str) -> dict | None: ...
    def mb_artist(self, mbid: str) -> dict | None: ...
    def mb_releases(self, mbid: str) -> dict: ...
    def mb_label_releases(self, label_mbid: str, max_releases: int) -> dict: ...
    def discogs_artist(self, discogs_id: str) -> dict | None: ...
    def discogs_releases(self, discogs_id: str) -> dict: ...
    def wikidata_influences(self, qid: str) -> dict | None: ...
    @property
    def has_discogs(self) -> bool: ...


class LiveApi:
    """Implémentation réelle, branchée sur les API publiques."""

    def __init__(self, settings: Settings):
        self.client = PoliteClient(settings.user_agent)
        self.token = settings.discogs_token

    @property
    def has_discogs(self) -> bool:
        return bool(self.token)

    def mb_search(self, name):
        return musicbrainz.search_artist(self.client, name)

    def mb_artist(self, mbid):
        return musicbrainz.get_artist(self.client, mbid)

    def mb_releases(self, mbid):
        return musicbrainz.browse_releases(self.client, mbid)

    def mb_label_releases(self, label_mbid, max_releases):
        return musicbrainz.browse_label_releases(self.client, label_mbid, max_releases)

    def discogs_artist(self, discogs_id):
        return discogs.get_artist(self.client, discogs_id, self.token)

    def discogs_releases(self, discogs_id):
        return discogs.get_artist_releases(self.client, discogs_id, self.token)

    def wikidata_influences(self, qid):
        return wikidata.get_influences(self.client, qid)


DEFAULT_LABEL_MAX_RELEASES = 300


class Crawler:
    def __init__(
        self,
        api: Api,
        store: RawStore,
        refresh_days: int = 30,
        label_max_releases: int = DEFAULT_LABEL_MAX_RELEASES,
    ):
        self.api = api
        self.store = store
        self.refresh_days = refresh_days
        # Au-delà de ce nombre de sorties, un label est trop gros pour définir une scène
        # (Polydor, Fiction, [PIAS]...) : on n'enrôle pas ses artistes. 0 = expansion désactivée.
        self.label_max_releases = label_max_releases
        self.failures = 0  # erreurs isolées pendant ce run (artistes, labels, seeds)

    def _failed(self, what: str, exc: Exception) -> None:
        """Journalise une erreur sans arrêter le crawl."""
        self.failures += 1
        log.error("Échec sur %s, réessayé au prochain run : %s: %s", what, type(exc).__name__, exc)

    def _cached(self, source: str, entity_type: str, entity_id: str, fetch, reuse=None):
        """Renvoie le payload en cache s'il est frais, sinon appelle l'API et le stocke.

        `reuse(payload)` peut refuser un payload en cache encore frais mais insuffisant.
        """
        if self.store.is_fresh(source, entity_type, entity_id, self.refresh_days):
            payload = self.store.get(source, entity_type, entity_id)
            if reuse is None or reuse(payload):
                return payload
        payload = fetch()
        self.store.put(source, entity_type, entity_id, payload)
        return payload

    # --- étape 1 : résoudre les artistes de départ en MBID ------------
    def resolve_seeds(self, seeds: list[dict]) -> None:
        for seed in seeds:
            name = seed["name"].strip()
            try:
                payload = self._cached(
                    "musicbrainz", "artist_search", name.casefold(), lambda: self.api.mb_search(name)
                )
            except Exception as exc:  # rien n'est mis en cache : la recherche sera refaite au prochain run
                self._failed(f"la recherche de « {name} »", exc)
                continue
            match = musicbrainz.best_match(payload, name)
            if not match:
                log.warning("Aucun résultat MusicBrainz pour « %s »", name)
                self.store.upsert_seed(name, seed.get("note"), None, None, None)
                continue
            mbid, matched_name, score = match
            if score < 90:
                log.warning("Correspondance incertaine pour « %s » → « %s » (score %s)", name, matched_name, score)
            self.store.upsert_seed(name, seed.get("note"), mbid, score, matched_name)
            self.store.enqueue(mbid, matched_name, depth=0, discovered_from=None, via="seed")

    # --- étape 2 : parcourir le graphe -------------------------------
    def process_artist(self, mbid: str, depth: int, max_depth: int) -> int:
        """Télécharge tout ce qui concerne un artiste. Renvoie le nombre de voisins ajoutés."""
        artist = self._cached("musicbrainz", "artist", mbid, lambda: self.api.mb_artist(mbid))
        if artist is None:
            raise LookupError(f"artiste MusicBrainz introuvable : {mbid}")
        releases = self._cached("musicbrainz", "artist_releases", mbid, lambda: self.api.mb_releases(mbid))

        ids = musicbrainz.external_ids(artist)
        if (d_id := ids.get("discogs")) and self.api.has_discogs:
            self._cached("discogs", "artist", d_id, lambda: self.api.discogs_artist(d_id))
            self._cached("discogs", "artist_releases", d_id, lambda: self.api.discogs_releases(d_id))

        influences = None
        if qid := ids.get("wikidata"):
            influences = self._cached("wikidata", "influences", qid, lambda: self.api.wikidata_influences(qid))

        if depth >= max_depth:
            return 0
        added = self.expand_relations(mbid, depth, artist, influences)
        added += self.expand_labels(mbid, depth, artist, releases)
        return added

    def expand_relations(self, mbid: str, depth: int, artist: dict, influences: dict | None) -> int:
        """Ajoute à la file les artistes liés par MusicBrainz (membres, alias...) et par Wikidata (P737)."""
        added = 0
        for n_mbid, n_name, rel_type in musicbrainz.related_artists(artist):
            added += self.store.enqueue(n_mbid, n_name, depth + 1, mbid, f"musicbrainz:{rel_type}")
        for n_mbid, n_name, direction in wikidata.related_artists(influences):
            added += self.store.enqueue(n_mbid, n_name, depth + 1, mbid, f"wikidata:{direction}")
        self.store.set_relations_expanded(mbid)
        return added

    # --- rattrapage quand on augmente --depth ---------------------------
    def catch_up(self, max_depth: int) -> None:
        """Étend les artistes déjà traités dont les voisins n'ont pas encore été ajoutés à la file :
        ceux qui étaient à la profondeur max d'un run précédent, ou crawlés avant l'expansion par labels.
        Les payloads artiste / sorties / influences viennent du cache : aucune requête, sauf pour les labels."""
        for mbid, name, depth in self.store.needing_relation_expansion(max_depth):
            try:
                artist = self.store.get("musicbrainz", "artist", mbid)
                if artist is None:
                    continue
                qid = musicbrainz.external_ids(artist).get("wikidata")
                influences = self.store.get("wikidata", "influences", qid) if qid else None
                added = self.expand_relations(mbid, depth, artist, influences)
                log.info("Relations de %s (profondeur %s) : +%s voisins", name, depth, added)
            except Exception as exc:  # relations_expanded reste faux : rattrapé au prochain run
                self._failed(f"les relations de {name} ({mbid})", exc)
        self.catch_up_label_expansion(max_depth)

    # --- expansion par labels de niche --------------------------------
    def _label_releases(self, label_mbid: str) -> dict | None:
        limit = self.label_max_releases

        def still_valid(payload: dict | None) -> bool:
            # Un payload tronqué (label jugé trop gros) n'est réutilisable que si le label
            # dépasse encore le seuil actuel ; sinon il faut le re-télécharger en entier.
            return payload is None or payload.get("complete", True) or payload.get("release-count", 0) > limit

        return self._cached(
            "musicbrainz", "label_releases", label_mbid,
            lambda: self.api.mb_label_releases(label_mbid, limit), reuse=still_valid,
        )

    def expand_labels(self, mbid: str, depth: int, artist: dict | None, releases: dict | None) -> int:
        """Ajoute à la file les autres artistes des labels de niche de cet artiste.

        Un label en erreur n'empêche pas d'explorer les autres ; l'artiste n'est alors pas marqué
        comme exploré, et ses labels seront repris au prochain run (les labels déjà téléchargés
        viennent du cache)."""
        if self.label_max_releases <= 0:
            return 0
        added = 0
        all_ok = True
        for label_mbid, label_name in musicbrainz.artist_labels(artist, releases).items():
            try:
                payload = self._label_releases(label_mbid)
            except Exception as exc:
                self._failed(f"le label « {label_name} » ({label_mbid})", exc)
                all_ok = False
                continue
            if not payload:
                continue
            n_releases = payload.get("release-count", 0)
            if n_releases > self.label_max_releases:
                log.info("Label « %s » ignoré : %s sorties (> %s)", label_name, n_releases, self.label_max_releases)
                continue
            for n_mbid, n_name in musicbrainz.label_artists(payload):
                if n_mbid != mbid:
                    added += self.store.enqueue(n_mbid, n_name, depth + 1, mbid, f"musicbrainz:label:{label_name}")
        if all_ok:
            self.store.set_label_expansion(mbid, self.label_max_releases)
        return added

    def catch_up_label_expansion(self, max_depth: int) -> None:
        """Explore les labels des artistes traités avant l'existence de l'expansion (ou avec un seuil
        plus bas). Les payloads artiste et sorties viennent du cache : seuls les labels sont demandés."""
        if self.label_max_releases <= 0:
            return
        for mbid, name, depth in self.store.needing_label_expansion(max_depth, self.label_max_releases):
            try:
                artist = self.store.get("musicbrainz", "artist", mbid)
                releases = self.store.get("musicbrainz", "artist_releases", mbid)
                added = self.expand_labels(mbid, depth, artist, releases)
                log.info("Labels de %s (profondeur %s) : +%s voisins", name, depth, added)
            except Exception as exc:
                self._failed(f"les labels de {name} ({mbid})", exc)

    def run(self, max_depth: int, max_artists: int) -> None:
        if retried := self.store.requeue_errors():
            log.info("%s artiste(s) en erreur au run précédent remis en attente", retried)
        self.catch_up(max_depth)
        processed = 0
        while processed < max_artists:
            item = self.store.next_pending(max_depth)
            if item is None:
                break
            mbid, name, depth = item
            try:
                added = self.process_artist(mbid, depth, max_depth)
                self.store.mark(mbid, "done")
                log.info("[%s] %s (profondeur %s) : +%s voisins", processed + 1, name, depth, added)
            except Exception as exc:  # on note l'erreur et on continue ; réessayé au prochain run
                self._failed(f"l'artiste {name} ({mbid})", exc)
                self.store.mark(mbid, "error", f"{type(exc).__name__}: {exc}"[:500])
            processed += 1
        log.info(
            "Terminé : %s traités dans ce run, %s en attente, %s en erreur, %s erreur(s) isolée(s) "
            "(réessayées au prochain run)",
            processed, self.store.count("pending"), self.store.count("error"), self.failures,
        )


def read_seeds(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row.get("name", "").strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl du graphe d'artistes")
    parser.add_argument("--seeds", default="seeds/seed_artists.csv")
    parser.add_argument("--depth", type=int, default=2, help="distance max depuis les artistes de départ")
    parser.add_argument("--max-artists", type=int, default=400, help="nombre max d'artistes traités par run")
    parser.add_argument(
        "--label-max-releases", type=int, default=DEFAULT_LABEL_MAX_RELEASES,
        help="n'explore que les labels ayant au plus N sorties (0 = pas d'expansion par labels)",
    )
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    if not settings.discogs_token:
        log.warning("DISCOGS_TOKEN absent : Discogs sera ignoré (pas de bios pour le LLM).")

    store = RawStore(settings.duckdb_path)
    api = LiveApi(settings)
    try:
        crawler = Crawler(api, store, settings.refresh_days, label_max_releases=args.label_max_releases)
        crawler.resolve_seeds(read_seeds(Path(args.seeds)))
        crawler.run(args.depth, args.max_artists)
    finally:
        api.client.close()
        store.close()


if __name__ == "__main__":
    main()
