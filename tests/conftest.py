"""Fixture partagée : une base de démo complète (raw + marts dbt), construite une fois par session."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from make_demo_db import make_demo_db

TRANSFORM = Path(__file__).parents[1] / "transform"


@pytest.fixture(scope="session")
def marts_db(tmp_path_factory) -> Path:
    """Base fictive passée par `dbt build` (modèles + tests dbt), dans un dossier temporaire."""
    pytest.importorskip("dbt.cli.main")
    tmp = tmp_path_factory.mktemp("marts")
    db = tmp / "demo.duckdb"
    make_demo_db(db)
    (tmp / "profiles.yml").write_text(
        f"lineage:\n  target: test\n  outputs:\n    test:\n      type: duckdb\n      path: '{db.as_posix()}'\n",
        encoding="utf-8",
    )
    # dans un sous-process : dbt-duckdb garde sa connexion ouverte tant que son process vit
    result = subprocess.run(
        [sys.executable, "-c", "from dbt.cli.main import cli; cli()",
         "build", "--project-dir", str(TRANSFORM), "--profiles-dir", str(tmp),
         "--target-path", str(tmp / "target"), "--log-path", str(tmp / "logs")],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    assert result.returncode == 0, f"dbt build a échoué :\n{result.stdout[-3000:]}"
    return db
