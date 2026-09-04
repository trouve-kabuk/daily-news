from __future__ import annotations

from pathlib import Path

import pytest

from app.config import Settings, load_settings
from app.knowledge.store import Store


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(Path("config"), tmp_path / "news.sqlite3")


@pytest.fixture
def store(settings: Settings) -> Store:
    result = Store(settings.database_path)
    result.initialize()
    return result
