from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import CoverageLane, TopicConfig


@dataclass(frozen=True)
class Article:
    id: int
    canonical_url: str
    source_id: str
    title: str
    excerpt: str | None
    author: str | None
    language: str
    published_at: str | None
    updated_at: str | None
    discovered_at: str
    content: str | None


def article_from_row(row: sqlite3.Row) -> Article:
    return Article(**{field: row[field] for field in Article.__dataclass_fields__})


def upsert_article(
    connection: sqlite3.Connection,
    *,
    canonical_url: str,
    stable_id: str | None,
    source_id: str,
    title: str,
    excerpt: str | None,
    author: str | None,
    language: str,
    published_at: datetime | None,
    updated_at: datetime | None,
    discovered_at: datetime,
    fetched_at: datetime,
    content: str | None,
) -> int:
    matches = connection.execute(
        "SELECT id FROM articles WHERE canonical_url=? OR (? IS NOT NULL AND stable_id=?)",
        (canonical_url, stable_id, stable_id),
    ).fetchall()
    if len(matches) > 1:
        raise ValueError("canonical URL and stable identifier refer to different articles")
    existing = matches[0] if matches else None
    values = (
        canonical_url,
        stable_id,
        source_id,
        title,
        excerpt,
        author,
        language,
        published_at.isoformat() if published_at else None,
        updated_at.isoformat() if updated_at else None,
        discovered_at.isoformat(),
        fetched_at.isoformat(),
        content,
    )
    if existing:
        connection.execute(
            """UPDATE articles SET canonical_url=?, stable_id=COALESCE(?, stable_id),
               title=?, excerpt=?, author=?, language=?, published_at=?,
               updated_at=?, last_fetched_at=?, content=COALESCE(?, content) WHERE id=?""",
            (
                canonical_url,
                stable_id,
                title,
                excerpt,
                author,
                language,
                published_at.isoformat() if published_at else None,
                updated_at.isoformat() if updated_at else None,
                fetched_at.isoformat(),
                content,
                existing["id"],
            ),
        )
        return int(existing["id"])
    cursor = connection.execute(
        """INSERT INTO articles
           (canonical_url, stable_id, source_id, title, excerpt, author, language,
            published_at, updated_at, discovered_at, first_fetched_at, last_fetched_at, content)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        values[:10] + (values[10], values[10], values[11]),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("article insert did not return an ID")
    return cursor.lastrowid


@dataclass(frozen=True)
class ReviewCandidate:
    article_id: int
    primary_lane_id: str


def matching_lanes(language: str, markets: set[str], topic: TopicConfig) -> list[CoverageLane]:
    return [
        lane
        for lane in topic.coverage
        if language in lane.languages and bool(markets.intersection(lane.markets))
    ]


def build_review_queue(
    connection: sqlite3.Connection,
    topic: TopicConfig,
    lane_id: str | None = None,
    *,
    exclude_decided: bool = True,
) -> list[ReviewCandidate]:
    requested_lane = next((lane for lane in topic.coverage if lane.id == lane_id), None)
    if lane_id and requested_lane is None:
        raise ValueError(f"unknown coverage lane {lane_id!r} for topic {topic.id!r}")
    feedback_filter = (
        """
           AND NOT EXISTS (
             SELECT 1 FROM feedback_events f
             WHERE f.topic_id=ta.topic_id AND f.article_id=a.id AND f.action != 'undo'
             AND NOT EXISTS (
               SELECT 1 FROM feedback_events u
               WHERE u.action='undo' AND u.compensates_event_id=f.id
             )
           )"""
        if exclude_decided
        else ""
    )
    rows = connection.execute(
        f"""SELECT a.*, GROUP_CONCAT(am.market) AS markets
           FROM topic_articles ta JOIN articles a ON a.id=ta.article_id
           LEFT JOIN article_markets am ON am.article_id=a.id
           WHERE ta.topic_id=? AND ta.topic_version=?
           {feedback_filter}
           GROUP BY a.id ORDER BY COALESCE(a.published_at, a.discovered_at) DESC, a.id""",
        (topic.id, topic.version),
    ).fetchall()
    candidates: list[ReviewCandidate] = []
    for row in rows:
        markets = set(str(row["markets"] or "").split(","))
        lanes = matching_lanes(str(row["language"]), markets, topic)
        if requested_lane:
            lanes = [item for item in lanes if item.id == requested_lane.id]
        if not lanes:
            continue
        candidates.append(ReviewCandidate(int(row["id"]), lanes[0].id))
    return candidates


def create_or_resume_session(
    connection: sqlite3.Connection, topic: TopicConfig, lane_id: str | None
) -> tuple[str, list[ReviewCandidate], int]:
    existing = connection.execute(
        """SELECT * FROM review_sessions WHERE topic_id=? AND topic_version=?
           AND lane_id IS ? AND status='active' ORDER BY created_at DESC LIMIT 1""",
        (topic.id, topic.version, lane_id),
    ).fetchone()
    if existing:
        queue = [ReviewCandidate(**item) for item in json.loads(existing["queue_json"])]
        return str(existing["id"]), queue, int(existing["position"])
    queue = build_review_queue(connection, topic, lane_id)
    session_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()
    connection.execute(
        """INSERT INTO review_sessions
           (id, topic_id, topic_version, lane_id, queue_json, position,
            created_at, updated_at, status) VALUES (?, ?, ?, ?, ?, 0, ?, ?, 'active')""",
        (
            session_id,
            topic.id,
            topic.version,
            lane_id,
            json.dumps([item.__dict__ for item in queue]),
            now,
            now,
        ),
    )
    return session_id, queue, 0
