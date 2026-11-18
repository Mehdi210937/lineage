"""Crée une base de démo avec les artistes fictifs, pour tester dbt sans réseau.

    python tests/make_demo_db.py data/demo.duckdb
    cd transform && dbt build --profiles-dir . --target demo
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from fake_api import FakeApi  # noqa: E402

from lineage.crawl import Crawler  # noqa: E402
from lineage.store import RawStore  # noqa: E402

path = Path(sys.argv[1] if len(sys.argv) > 1 else "data/demo.duckdb")
path.parent.mkdir(parents=True, exist_ok=True)
path.unlink(missing_ok=True)
store = RawStore(path)
crawler = Crawler(FakeApi(), store)
crawler.resolve_seeds([{"name": "Test Coven", "note": "seed fictif"}])
crawler.run(max_depth=2, max_artists=50)
store.close()
print(f"Base de démo créée : {path}")
