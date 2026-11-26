"""Wikipédia anglophone : texte brut des articles d'artistes, avec leurs sections.

1. Le QID Wikidata (trouvé via MusicBrainz) donne le titre de l'article anglais (sitelink enwiki),
   par l'API Wikidata `wbgetentities`.
2. L'API MediaWiki (extension TextExtracts) renvoie l'article en texte brut ; avec
   `exsectionformat=wiki`, les titres de sections restent sous la forme « == Titre == ».

Politique Wikimedia : User-Agent descriptif avec un contact (celui du projet, voir config.py) et
requêtes en série, sans parallélisme (le client limite à 1 requête/s par hôte).
https://meta.wikimedia.org/wiki/User-Agent_policy
"""
from __future__ import annotations

import re

from lineage.http import PoliteClient

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIPEDIA_API = "https://en.wikipedia.org/w/api.php"
QID_RE = re.compile(r"^Q\d+$")
HEADING_RE = re.compile(r"^(={2,6})\s*(.+?)\s*\1\s*$", re.MULTILINE)


def get_sitelinks(client: PoliteClient, qid: str) -> dict:
    """{"qid", "enwiki"} : titre de l'article anglais, ou None si l'entité n'en a pas."""
    if not QID_RE.match(qid):
        raise ValueError(f"QID invalide : {qid}")
    data = client.get_json(
        WIKIDATA_API,
        params={"action": "wbgetentities", "ids": qid, "props": "sitelinks", "sitefilter": "enwiki",
                "format": "json"},
    )
    entity = ((data or {}).get("entities") or {}).get(qid) or {}
    title = ((entity.get("sitelinks") or {}).get("enwiki") or {}).get("title")
    return {"qid": qid, "enwiki": title}


def get_article(client: PoliteClient, title: str) -> dict | None:
    """{"title", "pageid", "extract"} : l'article en texte brut, sections comprises. None si absent."""
    data = client.get_json(
        WIKIPEDIA_API,
        params={"action": "query", "prop": "extracts", "explaintext": 1, "exsectionformat": "wiki",
                "redirects": 1, "titles": title, "format": "json", "formatversion": 2},
    )
    pages = ((data or {}).get("query") or {}).get("pages") or []
    if not pages or pages[0].get("missing") or pages[0].get("invalid"):
        return None
    page = pages[0]
    return {"title": page.get("title", title), "pageid": page.get("pageid"), "extract": page.get("extract") or ""}


def split_sections(extract: str) -> list[tuple[str, int, str]]:
    """Découpe le texte en (titre, niveau, texte). L'introduction a le titre "" et le niveau 1."""
    sections = []
    matches = list(HEADING_RE.finditer(extract))
    intro_end = matches[0].start() if matches else len(extract)
    sections.append(("", 1, extract[:intro_end].strip()))
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(extract)
        sections.append((m.group(2).strip(), len(m.group(1)), extract[m.end():end].strip()))
    return sections
