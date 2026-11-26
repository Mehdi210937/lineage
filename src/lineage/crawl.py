"""Crawl en largeur du graphe d'artistes à partir des artistes de départ.

Usage :
    python -m lineage.crawl --depth 2 --max-artists 400
    python -m lineage.crawl --depth 2 --rescope [--apply]   # recalcule le périmètre de la file

Périmètre :
- le noyau : les artistes de départ, et ceux qui leur sont reliés par MusicBrainz (membres, alias,
  collaborations…) ou Wikidata (influences), jusqu'à --depth sauts ; seuls eux sont étendus ;
- les labels de la scène : au plus --label-max-releases sorties (100 par défaut) ET partagés par au
  moins --label-min-core-artists artistes du noyau (2 par défaut). Leurs artistes sont crawlés
  (fiche, sorties, bio) mais jamais étendus : un label ne fait pas entrer les labels de ses artistes.

Le crawl est reprenable : la file est stockée dans DuckDB (raw.crawl_queue).
Si tu l'interromps (Ctrl+C), relance la même commande, il reprend où il en était.
Une erreur sur un artiste ou un label (API saturée, 503…) n'arrête jamais le crawl : elle est
journalisée, l'élément est marqué pour être réessayé au prochain run, et le crawl continue.
Les réponses déjà en cache depuis moins de LINEAGE_REFRESH_DAYS jours ne sont pas re-téléchargées.
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from pathlib import Path
from typing import Protocol

from lineage.config import Settings, get_settings
from lineage.http import PoliteClient
from lineage.sources import discogs, musicbrainz, wikidata
from lineage.store import LABEL_VIA_PREFIX, RawStore

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


DEFAULT_LABEL_MAX_RELEASES = 100
DEFAULT_LABEL_MIN_CORE_ARTISTS = 2


class Crawler:
    def __init__(
        self,
        api: Api,
        store: RawStore,
        refresh_days: int = 30,
        label_max_releases: int = DEFAULT_LABEL_MAX_RELEASES,
        label_min_core_artists: int = DEFAULT_LABEL_MIN_CORE_ARTISTS,
    ):
        self.api = api
        self.store = store
        self.refresh_days = refresh_days
        # Au-delà de ce nombre de sorties, un label est trop gros pour définir une scène
        # (Polydor, Fiction, [PIAS]...) : on n'enrôle pas ses artistes. 0 = expansion désactivée.
        self.label_max_releases = label_max_releases
        # Un label n'appartient à la scène que s'il est partagé par assez d'artistes du noyau :
        # sinon chaque label d'un artiste un peu connu fait entrer des centaines d'inconnus.
        self.label_min_core_artists = label_min_core_artists
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
    def process_artist(self, mbid: str, depth: int, max_depth: int, expand: bool = True) -> int:
        """Télécharge tout ce qui concerne un artiste. Renvoie le nombre de voisins ajoutés.

        `expand=False` (artiste découvert par un label) : on télécharge ses données sans ajouter ses
        relations à la file."""
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

        if not expand or depth >= max_depth:
            return 0
        return self.expand_relations(mbid, depth, artist, influences)

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
        """Étend les artistes du noyau déjà traités dont les voisins n'ont pas encore été ajoutés à la
        file (ceux qui étaient à la profondeur max d'un run précédent). Les payloads viennent du cache."""
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

    # --- expansion par labels de la scène -----------------------------
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

    def shared_labels(self, core: dict[str, int], max_depth: int) -> dict[str, dict]:
        """Labels partagés par au moins `label_min_core_artists` artistes du noyau (depuis le cache).

        `core` : {mbid: profondeur}. Renvoie {label_mbid: {name, core_artists, depth}}, où depth est
        la plus petite profondeur d'un artiste du noyau sur ce label ; seuls les labels avec
        depth < max_depth sont gardés (leurs artistes arrivent à depth + 1)."""
        labels: dict[str, dict] = {}
        for mbid, depth in core.items():
            artist = self.store.get("musicbrainz", "artist", mbid)
            releases = self.store.get("musicbrainz", "artist_releases", mbid)
            for label_mbid, label_name in musicbrainz.artist_labels(artist, releases).items():
                lab = labels.setdefault(label_mbid, {"name": label_name, "core_artists": set(), "depth": depth})
                lab["core_artists"].add(mbid)
                lab["depth"] = min(lab["depth"], depth)
        return {
            k: v for k, v in labels.items()
            if len(v["core_artists"]) >= self.label_min_core_artists and v["depth"] < max_depth
        }

    def expand_core_labels(self, max_depth: int) -> int:
        """Ajoute à la file les artistes des labels de la scène : au plus `label_max_releases` sorties,
        et partagés par au moins `label_min_core_artists` artistes du noyau déjà traités.

        Un label en erreur est simplement réessayé au prochain appel (les autres sont explorés) :
        cette étape est recalculée à chaque run, à partir du cache."""
        if self.label_max_releases <= 0:
            return 0
        added = 0
        core = dict(self.store.core_done())
        for label_mbid, lab in self.shared_labels(core, max_depth).items():
            try:
                payload = self._label_releases(label_mbid)
            except Exception as exc:
                self._failed(f"le label « {lab['name']} » ({label_mbid})", exc)
                continue
            if not payload:
                continue
            n_releases = payload.get("release-count", 0)
            if n_releases > self.label_max_releases:
                log.info("Label « %s » ignoré : %s sorties (> %s)", lab["name"], n_releases, self.label_max_releases)
                continue
            source = min(lab["core_artists"])
            n = sum(
                self.store.enqueue(n_mbid, n_name, lab["depth"] + 1, source, f"{LABEL_VIA_PREFIX}{lab['name']}")
                for n_mbid, n_name in musicbrainz.label_artists(payload)
            )
            if n:
                log.info("Label « %s » (%s artistes du noyau) : +%s artistes", lab["name"], len(lab["core_artists"]), n)
            added += n
        return added

    # --- périmètre ----------------------------------------------------
    def compute_scope(self, max_depth: int) -> dict[str, tuple[str, str | None]]:
        """Recalcule le périmètre de chaque artiste de la file, depuis le cache (aucune requête).

        - core  : seeds, et artistes reliés à un artiste du noyau par MusicBrainz ou Wikidata
                  (jusqu'à max_depth) ;
        - label : artistes d'un label de la scène (seuil de sorties + partagé par assez d'artistes
                  du noyau) ;
        - out   : le reste, avec la raison.
        """
        rows = {r["mbid"]: r for r in self.store.queue_rows()}
        seeds = [r["mbid"] for r in rows.values() if r["discovered_via"] == "seed"]
        core: dict[str, int] = {m: 0 for m in seeds}
        frontier = list(seeds)
        while frontier:  # parcours en largeur sur les relations, depuis le cache
            nxt = []
            for mbid in frontier:
                depth = core[mbid]
                artist = self.store.get("musicbrainz", "artist", mbid)
                if depth >= max_depth or artist is None:
                    continue
                qid = musicbrainz.external_ids(artist).get("wikidata")
                influences = self.store.get("wikidata", "influences", qid) if qid else None
                neighbors = [m for m, _, _ in musicbrainz.related_artists(artist)]
                neighbors += [m for m, _, _ in wikidata.related_artists(influences)]
                for n in neighbors:
                    if n not in core:
                        core[n] = depth + 1
                        nxt.append(n)
            frontier = nxt

        done_core = {m: d for m, d in core.items() if rows.get(m, {}).get("status") == "done"}
        label_artists: dict[str, str] = {}
        for label_mbid, lab in self.shared_labels(done_core, max_depth).items():
            payload = self.store.get("musicbrainz", "label_releases", label_mbid)
            if not payload or not payload.get("complete", True) or payload.get("release-count", 0) > self.label_max_releases:
                continue
            for n_mbid, _ in musicbrainz.label_artists(payload):
                label_artists.setdefault(n_mbid, lab["name"])

        names = {m: r["name"] for m, r in rows.items()}
        scope: dict[str, tuple[str, str | None]] = {}
        for mbid, r in rows.items():
            via = r["discovered_via"] or ""
            if mbid in core:
                scope[mbid] = ("core", None)
            elif mbid in label_artists:
                scope[mbid] = ("label", f"label de la scène : {label_artists[mbid]}")
            elif via.startswith(LABEL_VIA_PREFIX):
                scope[mbid] = ("out", f"label hors périmètre : {via[len(LABEL_VIA_PREFIX):]}")
            else:
                parent = names.get(r["discovered_from"]) or r["discovered_from"]
                scope[mbid] = ("out", f"relation d'un artiste non étendu : {parent}")
        return scope

    def rescope(self, max_depth: int, apply: bool = False) -> dict:
        """Calcule le périmètre et, avec apply=True, l'écrit dans la file (réversible : rien n'est
        supprimé, les artistes hors périmètre en attente passent en out_of_scope). Renvoie un bilan."""
        scope = self.compute_scope(max_depth)
        rows = {r["mbid"]: r for r in self.store.queue_rows()}
        summary: dict = {"by_scope_and_status": {}, "pending_kept": 0, "pending_dropped": 0, "examples_out": []}
        for mbid, (s, reason) in scope.items():
            status = rows[mbid]["status"]
            key = f"{s}/{status}"
            summary["by_scope_and_status"][key] = summary["by_scope_and_status"].get(key, 0) + 1
            if status in ("pending", "error", "out_of_scope"):
                summary["pending_kept" if s != "out" else "pending_dropped"] += 1
            if s == "out" and status == "done" and len(summary["examples_out"]) < 15:
                summary["examples_out"].append(rows[mbid]["name"])
        if apply:
            self.store.set_scopes([(m, s, r) for m, (s, r) in scope.items()])
        return summary

    def run(self, max_depth: int, max_artists: int) -> None:
        if retried := self.store.requeue_errors():
            log.info("%s artiste(s) en erreur au run précédent remis en attente", retried)
        self.catch_up(max_depth)
        self.expand_core_labels(max_depth)
        processed = 0
        labels_stale = False  # de nouveaux artistes du noyau ont pu rendre des labels « partagés »
        current_depth = 0
        while processed < max_artists:
            item = self.store.next_pending(max_depth)
            if labels_stale and (item is None or item[2] > current_depth):
                labels_stale = False
                if self.expand_core_labels(max_depth):
                    continue  # de nouveaux artistes, peut-être moins profonds : on repioche
            if item is None:
                break
            mbid, name, depth = item
            current_depth = depth
            is_core = self.store.scope_of(mbid) == "core"
            try:
                added = self.process_artist(mbid, depth, max_depth, expand=is_core)
                self.store.mark(mbid, "done")
                labels_stale |= is_core
                log.info("[%s] %s (profondeur %s, %s) : +%s voisins",
                         processed + 1, name, depth, "noyau" if is_core else "label", added)
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
    parser.add_argument(
        "--label-min-core-artists", type=int, default=DEFAULT_LABEL_MIN_CORE_ARTISTS,
        help="n'explore que les labels partagés par au moins N artistes du noyau",
    )
    parser.add_argument(
        "--rescope", action="store_true",
        help="recalcule le périmètre de la file sans rien télécharger, affiche le bilan et s'arrête",
    )
    parser.add_argument("--apply", action="store_true", help="avec --rescope : écrit le nouveau périmètre")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = get_settings()
    store = RawStore(settings.duckdb_path)
    if args.rescope:
        try:
            crawler = Crawler(None, store, settings.refresh_days, args.label_max_releases, args.label_min_core_artists)
            summary = crawler.rescope(args.depth, apply=args.apply)
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print("Périmètre écrit." if args.apply else "Simulation : rien n'est écrit (ajoute --apply).")
        finally:
            store.close()
        return

    if not settings.discogs_token:
        log.warning("DISCOGS_TOKEN absent : Discogs sera ignoré (pas de bios pour le LLM).")
    api = LiveApi(settings)
    try:
        crawler = Crawler(api, store, settings.refresh_days, args.label_max_releases, args.label_min_core_artists)
        crawler.resolve_seeds(read_seeds(Path(args.seeds)))
        crawler.run(args.depth, args.max_artists)
    finally:
        api.client.close()
        store.close()


if __name__ == "__main__":
    main()
