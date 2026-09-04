from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import Settings, TopicConfig
from app.knowledge.topics import persist_topic
from app.offline.collection import (
    Collector,
    HttpFetcher,
    SourceFailure,
    begin_run,
    finish_run,
)
from app.offline.preparation import prepare_batch


@dataclass(frozen=True)
class CollectionReport:
    run_id: str
    sources_succeeded: int
    articles_prepared: int
    preparation_failures: int
    failures: list[SourceFailure]


def collect_topics(
    connection: sqlite3.Connection,
    settings: Settings,
    topics: list[TopicConfig],
    fetcher: HttpFetcher,
    *,
    now: datetime | None = None,
    sleep: Callable[[float], None] | None = None,
    on_source_start: Callable[[str], None] | None = None,
    on_retry: Callable[[str, str, float, int, int], None] | None = None,
) -> CollectionReport:
    current_time = now or datetime.now(UTC)
    for topic in topics:
        persist_topic(connection, topic)
    run_id = begin_run(connection, topics[0].id if len(topics) == 1 else None, current_time)
    collector = (
        Collector(fetcher, on_retry=on_retry)
        if sleep is None
        else Collector(fetcher, sleep, on_retry)
    )
    source_topics: dict[str, list[TopicConfig]] = {}
    for topic in topics:
        for source_id in topic.sources:
            source_topics.setdefault(source_id, []).append(topic)
    successes = prepared = preparation_failures = 0
    failures: list[SourceFailure] = []
    for source_id, matching_topics in source_topics.items():
        source = settings.sources[source_id]
        if not source.enabled:
            continue
        if on_source_start is not None:
            on_source_start(source.name)
        try:
            batch = collector.collect_source(connection, run_id, source, current_time)
            new_prepared, new_failed = prepare_batch(connection, batch, matching_topics)
            successes += 1
            prepared += new_prepared
            preparation_failures += new_failed
            if batch.warnings:
                failures.append(SourceFailure(source_id, "; ".join(batch.warnings)))
        except Exception as error:
            failures.append(SourceFailure(source_id, str(error)))
    finish_run(connection, run_id, current_time, bool(failures))
    return CollectionReport(run_id, successes, prepared, preparation_failures, failures)
