from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from app.config import TopicConfig


def persist_topic(connection: sqlite3.Connection, topic: TopicConfig) -> None:
    existing = connection.execute(
        "SELECT definition_hash FROM topic_versions WHERE topic_id = ? AND version = ?",
        (topic.id, topic.version),
    ).fetchone()
    if existing and existing["definition_hash"] != topic.definition_hash:
        raise ValueError(
            f"topic {topic.id!r} version {topic.version} changed; increment its version"
        )
    connection.execute(
        """INSERT OR IGNORE INTO topic_versions
           (topic_id, version, definition_hash, definition_json, created_at)
           VALUES (?, ?, ?, ?, ?)""",
        (
            topic.id,
            topic.version,
            topic.definition_hash,
            topic.model_dump_json(),
            datetime.now(UTC).isoformat(),
        ),
    )


def topic_history(connection: sqlite3.Connection, topic_id: str) -> list[dict[str, object]]:
    rows = connection.execute(
        "SELECT * FROM topic_versions WHERE topic_id = ? ORDER BY version", (topic_id,)
    ).fetchall()
    return [dict(row) | {"definition": json.loads(row["definition_json"])} for row in rows]
