from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime


@dataclass(frozen=True)
class Bookmark:
    topic_id: str
    article_id: int
    title: str
    source_id: str
    canonical_url: str
    created_at: str
    discussion_urls: str | None


def add_bookmark(
    connection: sqlite3.Connection,
    *,
    topic_id: str,
    article_id: int,
) -> bool:
    """Save one bookmark and return whether a new row was created."""
    inserted = connection.execute(
        """INSERT OR IGNORE INTO bookmarks (topic_id, article_id, created_at)
           VALUES (?, ?, ?)""",
        (topic_id, article_id, datetime.now(UTC).isoformat()),
    )
    return bool(inserted.rowcount)


def toggle_bookmark(
    connection: sqlite3.Connection,
    *,
    topic_id: str,
    article_id: int,
) -> bool:
    """Toggle one topic bookmark and return whether it is now saved."""
    deleted = connection.execute(
        "DELETE FROM bookmarks WHERE topic_id=? AND article_id=?",
        (topic_id, article_id),
    )
    if deleted.rowcount:
        return False
    add_bookmark(connection, topic_id=topic_id, article_id=article_id)
    return True


def remove_bookmark(
    connection: sqlite3.Connection,
    *,
    topic_id: str,
    article_id: int,
) -> bool:
    deleted = connection.execute(
        "DELETE FROM bookmarks WHERE topic_id=? AND article_id=?",
        (topic_id, article_id),
    )
    return bool(deleted.rowcount)


def list_bookmarks(
    connection: sqlite3.Connection,
    topic_id: str | None = None,
) -> list[Bookmark]:
    where = "WHERE b.topic_id=?" if topic_id else ""
    parameters = (topic_id,) if topic_id else ()
    rows = connection.execute(
        f"""SELECT b.topic_id, b.article_id, a.title, a.source_id, a.canonical_url,
                   b.created_at,
                   (SELECT GROUP_CONCAT(url, char(10)) FROM article_links
                    WHERE article_id=a.id AND kind='discussion') AS discussion_urls
            FROM bookmarks b JOIN articles a ON a.id=b.article_id
            {where}
            ORDER BY b.created_at DESC, b.id DESC""",
        parameters,
    ).fetchall()
    return [Bookmark(**dict(row)) for row in rows]
