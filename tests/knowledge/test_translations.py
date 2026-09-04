from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from app.config import Settings
from app.knowledge.articles import upsert_article
from app.knowledge.store import Store
from app.knowledge.translations import PROMPT_VERSION, translated_field


class CleanInference:
    model_version = "mlx-community/Qwen3.6-35B-A3B-4bit"

    def __init__(self) -> None:
        self.calls = 0

    def prepare(self) -> None:
        pass

    def translate(self, text: str, source_language: str, target_language: str = "en") -> str:
        self.calls += 1
        return "Clean current translation."

    def summarize(self, text: str, target_language: str = "en") -> str:
        raise AssertionError("summary was not requested")


def test_obsolete_translation_cache_is_ignored(settings: Settings, store: Store) -> None:
    now = datetime.now(UTC)
    source_text = "Texte source"
    inference = CleanInference()
    with store.transaction() as connection:
        article_id = upsert_article(
            connection,
            canonical_url="https://example.com/translated",
            stable_id=None,
            source_id="openai-news",
            title=source_text,
            excerpt=None,
            author=None,
            language="fr",
            published_at=now,
            updated_at=None,
            discovered_at=now,
            fetched_at=now,
            content=None,
        )
        connection.execute(
            """INSERT INTO translations
               (article_id, field, source_language, target_language, input_hash,
                model_version, prompt_version, translated_text, created_at)
               VALUES (?, 'title', 'fr', 'en', ?, ?, 'faithful-translation-v1', ?, ?)""",
            (
                article_id,
                hashlib.sha256(source_text.encode()).hexdigest(),
                inference.model_version,
                "<think>Old reasoning trace</think>Old translation.",
                now.isoformat(),
            ),
        )

        result = translated_field(
            connection,
            article_id=article_id,
            field="title",
            text=source_text,
            source_language="fr",
            inference=inference,
        )
        current_version = connection.execute(
            "SELECT prompt_version FROM translations ORDER BY id DESC LIMIT 1"
        ).fetchone()[0]

    assert result == "Clean current translation."
    assert inference.calls == 1
    assert current_version == PROMPT_VERSION
