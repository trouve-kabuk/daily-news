from __future__ import annotations

from datetime import UTC, datetime

from app.config import Settings
from app.knowledge.articles import upsert_article
from app.knowledge.feedback import effective_feedback, feedback_history, record_feedback
from app.knowledge.store import Store


def test_feedback_is_append_only_and_undo_compensates(settings: Settings, store: Store) -> None:
    now = datetime.now(UTC)
    with store.transaction() as connection:
        article_id = upsert_article(
            connection,
            canonical_url="https://example.com/one",
            stable_id=None,
            source_id="openai-news",
            title="One",
            excerpt=None,
            author=None,
            language="en",
            published_at=now,
            updated_at=None,
            discovered_at=now,
            fetched_at=now,
            content=None,
        )
        record_feedback(
            connection,
            topic_id="ai-engineering",
            topic_version=1,
            article_id=article_id,
            action="yes",
        )
        record_feedback(
            connection,
            topic_id="ai-engineering",
            topic_version=1,
            article_id=article_id,
            action="undo",
        )
        assert effective_feedback(connection, "ai-engineering", article_id) is None
        history = feedback_history(connection, "ai-engineering", article_id)
        assert [item["action"] for item in history] == ["yes", "undo"]
