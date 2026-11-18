"""Crawl en largeur du graphe d'artistes à partir des artistes de départ.

Usage :
    python -m lineage.crawl --depth 2 --max-artists 400

Le crawl est reprenable : la file est stockée dans DuckDB (raw.crawl_queue).
Si tu l'interromps (Ctrl+C), relance la même commande, il reprend où il en était.
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

    def discogs_artist(self, discogs_id):
        return discogs.get_artist(self.client, discogs_id, self.token)

    def discogs_releases(self, discogs_id):
        return discogs.get_artist_releases(self.client, discogs_id, self.token)

    def wikidata_influences(self, qid):
        return wikidata.get_influences(self.client, qid)


class Crawler:
    def __init__(self, api: Api, store: RawStore, refresh_days: int = 30):
        self.api = api
        self.store = store
        self.refresh_days = refresh_days

    def _cached(self, source: str, entity_type: str, entity_id: str, fetch):
        """Renvoie le payload en cache s'il est frais, sinon appelle l'API et le stocke."""
        if self.store.is_fresh(source, entity_type, entity_id, self.refresh_days):
            return self.store.get(source, entity_type, entity_id)
        payload = fetch()
        self.store.put(source, entity_type, entity_id, payload)
        return payload

    # --- étape 1 : résoudre les artistes de départ en MBID ------------
    def resolve_seeds(self, seeds: list[dict]) -> None:
        for seed in seeds:
            name = seed["name"].strip()
            payload = self._cached("musicbrainz", "artist_search", name.casefold(), lambda: self.api.mb_search(name))
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
        self._cached("musicbrainz", "artist_releases", mbid, lambda: self.api.mb_releases(mbid))

        ids = musicbrainz.external_ids(artist)
        if (d_id := ids.get("discogs")) and self.api.has_discogs:
            self._cached("discogs", "artist", d_id, lambda: self.api.discogs_artist(d_id))
            self._cached("discogs", "artist_releases", d_id, lambda: self.api.discogs_releases(d_id))

        influences = None
        if qid := ids.get("wikidata"):
            influences = self._cached("wikidata", "influences", qid, lambda: self.api.wikidata_influences(qid))

        if depth >= max_depth:
            return 0
        added = 0
        for n_mbid, n_name, rel_type in musicbrainz.related_artists(artist):
            added += self.store.enqueue(n_mbid, n_name, depth + 1, mbid, f"musicbrainz:{rel_type}")
        for n_mbid, n_name, direction in wikidata.related_artists(influences):
            added += self.store.enqueue(n_mbid, n_name, depth + 1, mbid, f"wikidata:{direction}")
        return added

    def run(self, max_depth: int, max_artists: int) -> None:
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
            except Exception as exc:  # on note l'erreur et on continue le crawl
                log.exception("Échec sur %s (%s)", name, mbid)
                self.store.mark(mbid, "error", str(exc)[:500])
            processed += 1
        log.info(
            "Terminé : %s traités dans ce run, %s en attente, %s en erreur",
            processed, self.store.count("pending"), self.store.count("error"),
        )


def read_seeds(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return [row for row in csv.DictReader(f) if row.get("name", "").strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Crawl du graphe d'artistes")
    parser.add_argument("--seeds", default="seeds/seed_artists.csv")
    parser.add_argument("--depth", type=int, default=2, help="distance max depuis les artistes de départ")
    parser.add_argument("--max-artists", type=int, default=400, help="nombre max d'artistes traités par run")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    if not settings.discogs_token:
        log.warning("DISCOGS_TOKEN absent : Discogs sera ignoré (pas de bios pour le LLM).")

    store = RawStore(settings.duckdb_path)
    api = LiveApi(settings)
    try:
        crawler = Crawler(api, store, settings.refresh_days)
        crawler.resolve_seeds(read_seeds(Path(args.seeds)))
        crawler.run(args.depth, args.max_artists)
    finally:
        api.client.close()
        store.close()


if __name__ == "__main__":
    main()
