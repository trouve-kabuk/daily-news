from __future__ import annotations

from datetime import UTC, datetime

from app.config import Settings
from app.knowledge.articles import upsert_article
from app.knowledge.bookmarks import add_bookmark, list_bookmarks, remove_bookmark, toggle_bookmark
from app.knowledge.store import Store


def test_bookmarks_can_be_toggled_listed_and_removed(settings: Settings, store: Store) -> None:
    now = datetime.now(UTC)
    with store.transaction() as connection:
        article_id = upsert_article(
            connection,
            canonical_url="https://example.com/bookmarked",
            stable_id=None,
            source_id="openai-news",
            title="Saved article",
            excerpt=None,
            author=None,
            language="en",
            published_at=now,
            updated_at=None,
            discovered_at=now,
            fetched_at=now,
            content=None,
        )

        assert toggle_bookmark(connection, topic_id="ai-engineering", article_id=article_id)
        assert not add_bookmark(connection, topic_id="ai-engineering", article_id=article_id)
        saved = list_bookmarks(connection, "ai-engineering")
        assert [(item.article_id, item.title) for item in saved] == [(article_id, "Saved article")]
        assert not toggle_bookmark(connection, topic_id="ai-engineering", article_id=article_id)
        assert list_bookmarks(connection) == []
        assert not remove_bookmark(connection, topic_id="ai-engineering", article_id=article_id)
