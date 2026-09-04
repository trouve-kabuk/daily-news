from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

FeedbackAction = Literal["yes", "no", "maybe", "hide", "undo"]


@dataclass(frozen=True)
class EffectiveFeedback:
    action: FeedbackAction
    reason: str | None
    event_id: int


def record_feedback(
    connection: sqlite3.Connection,
    *,
    topic_id: str,
    topic_version: int,
    article_id: int,
    action: FeedbackAction,
    reason: str | None = None,
    session_id: str | None = None,
    ranking_version: str = "review-queue-v1",
) -> int:
    compensates: int | None = None
    if action == "undo":
        current = effective_feedback(connection, topic_id, article_id)
        if current is None:
            raise ValueError("there is no effective feedback to undo")
        compensates = current.event_id
    cursor = connection.execute(
        """INSERT INTO feedback_events
           (topic_id, topic_version, article_id, action, reason, created_at,
            session_id, ranking_version, compensates_event_id)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            topic_id,
            topic_version,
            article_id,
            action,
            reason,
            datetime.now(UTC).isoformat(),
            session_id,
            ranking_version,
            compensates,
        ),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("feedback insert did not return an ID")
    return cursor.lastrowid


def effective_feedback(
    connection: sqlite3.Connection, topic_id: str, article_id: int
) -> EffectiveFeedback | None:
    rows = connection.execute(
        """SELECT id, action, reason, compensates_event_id FROM feedback_events
           WHERE topic_id = ? AND article_id = ? ORDER BY id""",
        (topic_id, article_id),
    ).fetchall()
    active: list[sqlite3.Row] = []
    compensated: set[int] = set()
    for row in rows:
        if row["action"] == "undo":
            compensated_id = row["compensates_event_id"]
            if compensated_id is not None:
                compensated.add(int(compensated_id))
        else:
            active.append(row)
    for row in reversed(active):
        if int(row["id"]) not in compensated:
            return EffectiveFeedback(row["action"], row["reason"], int(row["id"]))
    return None


def feedback_history(
    connection: sqlite3.Connection, topic_id: str, article_id: int
) -> list[dict[str, object]]:
    return [
        dict(row)
        for row in connection.execute(
            """SELECT * FROM feedback_events WHERE topic_id = ? AND article_id = ?
               ORDER BY id""",
            (topic_id, article_id),
        )
    ]
