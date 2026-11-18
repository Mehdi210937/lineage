"""Configuration lue depuis les variables d'environnement (.env)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    contact: str
    discogs_token: str | None
    data_dir: Path
    refresh_days: int
    ollama_url: str
    ollama_model: str

    @property
    def duckdb_path(self) -> Path:
        return self.data_dir / "lineage.duckdb"

    @property
    def user_agent(self) -> str:
        # MusicBrainz exige un User-Agent identifiable avec un contact.
        return f"lineage/0.1 ( {self.contact} )"


def get_settings() -> Settings:
    data_dir = Path(os.getenv("LINEAGE_DATA_DIR", "./data")).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        contact=os.getenv("LINEAGE_CONTACT", "https://github.com/Mehdi210937/lineage"),
        discogs_token=os.getenv("DISCOGS_TOKEN") or None,
        data_dir=data_dir,
        refresh_days=int(os.getenv("LINEAGE_REFRESH_DAYS", "30")),
        ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
        ollama_model=os.getenv("OLLAMA_MODEL", "qwen3:8b"),
    )
