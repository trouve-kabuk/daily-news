from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS topic_versions (
    topic_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    definition_hash TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (topic_id, version)
);
CREATE TABLE IF NOT EXISTS collection_runs (
    id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    topic_id TEXT,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_fetches (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES collection_runs(id),
    source_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    http_status INTEGER,
    etag TEXT,
    last_modified TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS source_fetches_latest
    ON source_fetches(source_id, id DESC);
CREATE TABLE IF NOT EXISTS articles (
    id INTEGER PRIMARY KEY,
    canonical_url TEXT NOT NULL UNIQUE,
    stable_id TEXT UNIQUE,
    source_id TEXT NOT NULL,
    title TEXT NOT NULL,
    excerpt TEXT,
    author TEXT,
    language TEXT NOT NULL,
    published_at TEXT,
    updated_at TEXT,
    discovered_at TEXT NOT NULL,
    first_fetched_at TEXT NOT NULL,
    last_fetched_at TEXT NOT NULL,
    content TEXT
);
CREATE TABLE IF NOT EXISTS article_links (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES articles(id),
    kind TEXT NOT NULL CHECK(kind IN ('discussion')),
    source_id TEXT NOT NULL,
    url TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    UNIQUE(article_id, kind, url)
);
CREATE INDEX IF NOT EXISTS article_links_by_article
    ON article_links(article_id, kind, id);
CREATE TABLE IF NOT EXISTS source_documents (
    id INTEGER PRIMARY KEY,
    fetch_id INTEGER NOT NULL REFERENCES source_fetches(id),
    article_id INTEGER REFERENCES articles(id),
    source_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    response_metadata_json TEXT NOT NULL,
    raw_metadata_json TEXT NOT NULL,
    extracted_content TEXT,
    extraction_status TEXT NOT NULL,
    failure_reason TEXT
);
CREATE TABLE IF NOT EXISTS article_markets (
    article_id INTEGER NOT NULL REFERENCES articles(id),
    market TEXT NOT NULL,
    confidence REAL NOT NULL,
    evidence TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    PRIMARY KEY(article_id, market, classifier_version)
);
CREATE TABLE IF NOT EXISTS topic_articles (
    topic_id TEXT NOT NULL,
    topic_version INTEGER NOT NULL,
    article_id INTEGER NOT NULL REFERENCES articles(id),
    source_id TEXT NOT NULL,
    discovered_at TEXT NOT NULL,
    PRIMARY KEY(topic_id, topic_version, article_id)
);
CREATE TABLE IF NOT EXISTS assessments (
    id INTEGER PRIMARY KEY,
    topic_id TEXT NOT NULL,
    topic_version INTEGER NOT NULL,
    article_id INTEGER NOT NULL REFERENCES articles(id),
    candidate_signals_json TEXT NOT NULL,
    scores_json TEXT NOT NULL,
    decision TEXT NOT NULL,
    explanation TEXT NOT NULL,
    retrieval_version TEXT NOT NULL,
    ranking_version TEXT NOT NULL,
    model_version TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS translations (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES articles(id),
    field TEXT NOT NULL,
    source_language TEXT NOT NULL,
    target_language TEXT NOT NULL,
    input_hash TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    translated_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(article_id, field, target_language, input_hash, model_version, prompt_version)
);
CREATE TABLE IF NOT EXISTS processing_outcomes (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES articles(id),
    stage TEXT NOT NULL,
    field TEXT,
    status TEXT NOT NULL,
    reason TEXT NOT NULL,
    processor_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS article_content_versions (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES articles(id),
    requested_url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    http_status INTEGER NOT NULL,
    response_headers_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    extracted_text TEXT,
    extraction_status TEXT NOT NULL,
    failure_reason TEXT
);
CREATE TABLE IF NOT EXISTS article_summaries (
    id INTEGER PRIMARY KEY,
    article_id INTEGER NOT NULL REFERENCES articles(id),
    content_version_id INTEGER NOT NULL REFERENCES article_content_versions(id),
    input_hash TEXT NOT NULL,
    target_language TEXT NOT NULL,
    model_version TEXT NOT NULL,
    prompt_version TEXT NOT NULL,
    summary_text TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(article_id, input_hash, target_language, model_version, prompt_version)
);
CREATE TABLE IF NOT EXISTS review_sessions (
    id TEXT PRIMARY KEY,
    topic_id TEXT NOT NULL,
    topic_version INTEGER NOT NULL,
    lane_id TEXT,
    queue_json TEXT NOT NULL,
    position INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS feedback_events (
    id INTEGER PRIMARY KEY,
    topic_id TEXT NOT NULL,
    topic_version INTEGER NOT NULL,
    article_id INTEGER NOT NULL REFERENCES articles(id),
    action TEXT NOT NULL CHECK(action IN ('yes', 'no', 'maybe', 'hide', 'undo')),
    reason TEXT,
    created_at TEXT NOT NULL,
    session_id TEXT,
    ranking_version TEXT NOT NULL,
    compensates_event_id INTEGER REFERENCES feedback_events(id)
);
CREATE INDEX IF NOT EXISTS feedback_by_topic_article
    ON feedback_events(topic_id, article_id, id);
CREATE TABLE IF NOT EXISTS bookmarks (
    id INTEGER PRIMARY KEY,
    topic_id TEXT NOT NULL,
    article_id INTEGER NOT NULL REFERENCES articles(id),
    created_at TEXT NOT NULL,
    UNIQUE(topic_id, article_id)
);
CREATE INDEX IF NOT EXISTS bookmarks_recent
    ON bookmarks(topic_id, created_at DESC);
CREATE TABLE IF NOT EXISTS feed_entries (
    id INTEGER PRIMARY KEY,
    topic_id TEXT NOT NULL,
    topic_version INTEGER NOT NULL,
    article_id INTEGER NOT NULL REFERENCES articles(id),
    edition_date TEXT NOT NULL,
    primary_lane_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    ranking_version TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(topic_id, article_id)
);
"""


class Store:
    def __init__(self, path: Path) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def initialize(self) -> None:
        connection = self.connect()
        try:
            connection.executescript(SCHEMA)
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()
