"""Coach de production : conseils fondés UNIQUEMENT sur le graphe et les bios, chaque conseil sourcé.

1. `build_facts` transforme les données des inspirations choisies en faits numérotés (S1, S2…),
   chacun avec sa source (MusicBrainz, Wikidata, bio Discogs, LLM + citation).
2. Le LLM (Ollama, qwen3:8b par défaut) répond en JSON contraint : une liste de conseils, chacun
   avec les identifiants des faits qui le justifient.
3. `validate_answer` écarte tout conseil sans source ou qui cite un fait inexistant : comme pour
   l'extraction, le LLM ne peut pas inventer de source.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import httpx
from pydantic import BaseModel, Field

from lineage.explore.db import EDGE_IN_PERIMETER, query
from lineage.explore.graph import EDGE_LABELS

MAX_BIO_CHARS = 1500
MAX_EDGES_PER_ARTIST = 15
MAX_LABELS_PER_ARTIST = 8

SOURCE_NAMES = {
    "musicbrainz": "MusicBrainz",
    "wikidata": "Wikidata",
    "llm": "LLM (extrait de la bio Discogs)",
    "discogs": "Bio Discogs",
}


@dataclass(frozen=True)
class Fact:
    id: str
    artist: str
    source: str            # nom lisible de la source
    text: str
    citation: str | None = None

    def render(self) -> str:
        cite = f' — citation : « {self.citation} »' if self.citation else ""
        return f"[{self.id}] ({self.source}) {self.text}{cite}"


class Advice(BaseModel):
    conseil: str = Field(description="Un conseil de production concret, en une ou deux phrases")
    sources: list[str] = Field(description="Identifiants des faits qui justifient ce conseil, ex. ['S1', 'S4']")


class CoachAnswer(BaseModel):
    conseils: list[Advice] = Field(min_length=1, description="Entre 3 et 8 conseils, chacun sourcé")
    limites: str = Field(description="Ce que les faits fournis ne permettent pas de conseiller")


SYSTEM_PROMPT = """Tu es un coach de production musicale. Tu aides un producteur à s'inspirer d'artistes.
Règles strictes :
- Tu t'appuies UNIQUEMENT sur les faits numérotés fournis ([S1], [S2]…), jamais sur tes connaissances.
- Donne entre 3 et 8 conseils. Chaque conseil cite dans « sources » les identifiants des faits qui le
  justifient (au moins un). Un conseil qu'aucun fait ne justifie ne doit pas être donné.
- Les conseils sont concrets (sons, textures, voix, tempo, structure, influences à écouter, labels à viser)
  et en français.
- Dans « limites », dis honnêtement ce que les faits ne permettent pas de conseiller.
Réponds uniquement en JSON conforme au schéma."""


def _year_span(begin, end) -> str:
    if begin and end:
        return str(begin) if begin == end else f"{begin}–{end}"
    return f"depuis {begin}" if begin else ""


def build_facts(db_path: Path | str, mbids: list[str], include_out: bool = False) -> list[Fact]:
    facts: list[Fact] = []

    def add(artist: str, source: str, text: str, citation: str | None = None) -> None:
        facts.append(Fact(f"S{len(facts) + 1}", artist, source, text, citation))

    for mbid in mbids:
        rows = query(db_path, "select * from marts.dim_artists where artist_mbid = ?", [mbid])
        if not rows:
            continue
        a = rows[0]
        name = a["artist_name"]

        identity = [x for x in (a["artist_type"], a["country_code"], _year_span(a["begin_year"], a["end_year"])) if x]
        if identity:
            add(name, "MusicBrainz", f"{name} : {', '.join(map(str, identity))}.")
        if a["genres"]:
            add(name, "MusicBrainz", f"Genres de {name} : {', '.join(a['genres'])}.")
        if a["tags"]:
            add(name, "MusicBrainz", f"Tags de {name} : {', '.join(a['tags'][:15])}.")
        if a["discogs_profile"]:
            bio = a["discogs_profile"]
            bio = bio if len(bio) <= MAX_BIO_CHARS else bio[:MAX_BIO_CHARS] + "…"
            add(name, SOURCE_NAMES["discogs"], f"Bio de {name} : {bio}")

        edges = query(
            db_path,
            f"""
            select * from marts.edges_artists e
            where (from_artist_mbid = ? or to_artist_mbid = ?)
              and (? or {EDGE_IN_PERIMETER})
            -- les influences et comparaisons d'abord : elles parlent du son
            order by case source_system when 'llm' then 0 when 'wikidata' then 1 else 2 end,
                     confidence desc nulls last, edge_type
            limit ?
            """,
            [mbid, mbid, include_out, MAX_EDGES_PER_ARTIST],
        )
        for e in edges:
            src = e["from_artist_name"] or e["from_artist_mbid"][:8]
            dst = e["to_artist_name"] or e["to_artist_mbid"][:8]
            citation = e["evidence"] if e["source_system"] == "llm" else None
            add(name, SOURCE_NAMES[e["source_system"]], f"{src} {EDGE_LABELS.get(e['edge_type'], e['edge_type'])} {dst}.",
                citation)

        labels = query(
            db_path,
            """
            select b.*, l.n_releases_total
            from marts.bridge_artist_labels b
            left join marts.dim_labels l using (label_mbid)
            where b.artist_mbid = ?
            order by b.n_releases desc limit ?
            """,
            [mbid, MAX_LABELS_PER_ARTIST],
        )
        for lab in labels:
            span = _year_span(lab["first_release_year"], lab["last_release_year"])
            size = f", label de {lab['n_releases_total']} sorties au total" if lab["n_releases_total"] else ""
            add(name, "MusicBrainz",
                f"{name} a sorti {lab['n_releases']} disque(s) sur {lab['label_name']}"
                f"{' (' + span + ')' if span else ''}{size}.")
    return facts


def build_messages(facts: list[Fact], inspirations: list[str], goal: str = "") -> list[dict]:
    user = (
        f"Inspirations choisies : {', '.join(inspirations)}.\n"
        + (f"Ce que je veux produire : {goal.strip()}\n" if goal.strip() else "")
        + "\nFaits disponibles :\n"
        + "\n".join(f.render() for f in facts)
        + "\n\nDonne-moi des conseils de production fondés sur ces faits."
    )
    return [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]


def ask_coach(client: httpx.Client, url: str, model: str, messages: list[dict]) -> CoachAnswer:
    resp = client.post(
        f"{url}/api/chat",
        json={
            "model": model,
            "stream": False,
            "think": False,                             # qwen3 : pas de raisonnement visible
            "format": CoachAnswer.model_json_schema(),  # sortie contrainte au schéma
            "options": {"temperature": 0.3},
            "messages": messages,
        },
        timeout=300,
    )
    resp.raise_for_status()
    return CoachAnswer.model_validate_json(resp.json()["message"]["content"])


def validate_answer(answer: CoachAnswer, facts: list[Fact]) -> tuple[list[Advice], list[Advice]]:
    """(conseils gardés, conseils rejetés). Rejeté = aucune source, ou une source qui n'existe pas."""
    known = {f.id for f in facts}
    kept, rejected = [], []
    for a in answer.conseils:
        ids = [i.strip().strip("[]") for i in a.sources]
        if ids and all(i in known for i in ids):
            kept.append(Advice(conseil=a.conseil, sources=list(dict.fromkeys(ids))))
        else:
            rejected.append(a)
    return kept, rejected


def ollama_status(client: httpx.Client, url: str, model: str) -> tuple[bool, str]:
    """Vérifie qu'Ollama répond et que le modèle est téléchargé."""
    try:
        resp = client.get(f"{url}/api/tags", timeout=5)
        resp.raise_for_status()
    except httpx.HTTPError:
        return False, f"Ollama ne répond pas sur {url}. Lance l'application Ollama (ou `ollama serve`)."
    names = {m.get("name") for m in resp.json().get("models", [])}
    if model not in names and f"{model}:latest" not in names:
        return False, f"Modèle {model} absent. Télécharge-le avec `ollama pull {model}`."
    return True, f"Ollama prêt ({model})."
