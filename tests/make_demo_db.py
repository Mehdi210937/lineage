"""Crée une base de démo avec les artistes fictifs, pour tester dbt et l'app sans réseau.

Le crawl suit d'abord des règles larges (tout label jusqu'à 10 000 sorties), puis --rescope applique
le périmètre actuel : Pop Star, venu du gros label, reste en base mais passe hors périmètre.
Ajoute aussi des affirmations LLM fictives (raw.llm_influence_claims), sans appeler de LLM.

    python tests/make_demo_db.py data/demo.duckdb
    cd transform && dbt build --profiles-dir . --target demo
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fake_api import DEMO_LLM_CLAIMS, FakeApi  # noqa: E402

from lineage.crawl import Crawler  # noqa: E402
from lineage.store import RawStore  # noqa: E402


def make_demo_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    store = RawStore(path)
    crawler = Crawler(FakeApi(), store, label_max_releases=10_000, label_min_core_artists=1)
    crawler.resolve_seeds([{"name": "Test Coven", "note": "seed fictif"}])
    crawler.run(max_depth=2, max_artists=50)
    crawler.label_max_releases, crawler.label_min_core_artists = 100, 2
    crawler.rescope(max_depth=2, apply=True)
    store.con.executemany(
        "insert into raw.llm_influence_claims values (?, 'demo-fictif', ?, ?, ?, ?, ?)",
        [(*claim, datetime(2026, 1, 1)) for claim in DEMO_LLM_CLAIMS],
    )
    store.close()


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "data/demo.duckdb")
    make_demo_db(target)
    print(f"Base de démo créée : {target}")
