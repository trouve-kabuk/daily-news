from __future__ import annotations

import sqlite3
from datetime import UTC, date, datetime

from app.config import TopicConfig
from app.knowledge.articles import ReviewCandidate, build_review_queue
from app.knowledge.feedback import effective_feedback, record_feedback


def create_edition(
    connection: sqlite3.Connection, topic: TopicConfig, edition_date: date | None = None
) -> list[sqlite3.Row]:
    day = (edition_date or datetime.now(UTC).date()).isoformat()
    candidates = build_review_queue(connection, topic, exclude_decided=False)
    eligible = []
    for candidate in candidates:
        effective = effective_feedback(connection, topic.id, candidate.article_id)
        if effective is not None and effective.action == "yes":
            eligible.append(candidate)

    lane_counts: dict[str, int] = {}
    selected: list[int] = []

    def add(candidate: ReviewCandidate) -> bool:
        article_id = candidate.article_id
        lane_id = candidate.primary_lane_id
        if article_id in selected or len(selected) >= topic.edition.max_articles:
            return False
        lane = next(item for item in topic.coverage if item.id == lane_id)
        if lane.max_articles is not None and lane_counts.get(lane.id, 0) >= lane.max_articles:
            return False
        selected.append(article_id)
        lane_counts[lane.id] = lane_counts.get(lane.id, 0) + 1
        return True

    # First honor soft targets with qualifying articles; targets never create eligibility.
    for lane in topic.coverage:
        target = lane.target_articles or 0
        for candidate in eligible:
            if lane_counts.get(lane.id, 0) >= target:
                break
            if candidate.primary_lane_id == lane.id:
                add(candidate)
    # Then fill remaining capacity by stored recency ordering without exceeding hard caps.
    for candidate in eligible:
        add(candidate)

    for position, article_id in enumerate(selected, start=1):
        candidate = next(item for item in eligible if item.article_id == article_id)
        lane = next(item for item in topic.coverage if item.id == candidate.primary_lane_id)
        connection.execute(
            """INSERT OR IGNORE INTO feed_entries
               (topic_id, topic_version, article_id, edition_date, primary_lane_id,
                position, ranking_version, created_at)
               VALUES (?, ?, ?, ?, ?, ?, 'approved-v1', ?)""",
            (
                topic.id,
                topic.version,
                candidate.article_id,
                day,
                lane.id,
                position,
                datetime.now(UTC).isoformat(),
            ),
        )
    if not selected:
        return []
    placeholders = ",".join("?" for _ in selected)
    return connection.execute(
        f"""SELECT fe.*, a.title, a.canonical_url, a.source_id,
                    (SELECT GROUP_CONCAT(url, char(10)) FROM article_links
                     WHERE article_id=a.id AND kind='discussion') AS discussion_urls
             FROM feed_entries fe
             JOIN articles a ON a.id=fe.article_id WHERE fe.topic_id=?
             AND fe.edition_date=? AND fe.article_id IN ({placeholders}) ORDER BY fe.position""",
        (topic.id, day, *selected),
    ).fetchall()


def hide_entry(
    connection: sqlite3.Connection, topic: TopicConfig, article_id: int, reason: str | None
) -> None:
    record_feedback(
        connection,
        topic_id=topic.id,
        topic_version=topic.version,
        article_id=article_id,
        action="hide",
        reason=reason,
        ranking_version="approved-v1",
    )
