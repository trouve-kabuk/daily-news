from __future__ import annotations

import html
import json
import re
import sqlite3
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from app.config import TopicConfig
from app.knowledge.articles import upsert_article
from app.offline.collection import CollectedDocument, SourceBatch, metadata_json, within_lookback

TRACKING_PARAMETERS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


def normalize_text(value: str | None) -> str | None:
    if value is None:
        return None
    without_tags = re.sub(r"<[^>]+>", " ", html.unescape(value))
    normalized = " ".join(without_tags.split())
    return normalized or None


def canonicalize_url(value: str) -> str:
    parts = urlsplit(value.strip())
    scheme = parts.scheme.lower() or "https"
    hostname = (parts.hostname or "").lower()
    port = parts.port
    netloc = hostname
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{hostname}:{port}"
    query = urlencode(
        sorted(
            (key, val)
            for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMETERS
        )
    )
    path = re.sub(r"/{2,}", "/", parts.path) or "/"
    if path != "/":
        path = path.rstrip("/")
    return urlunsplit((scheme, netloc, path, query, ""))


def canonical_language(value: str) -> str:
    lowered = value.strip().replace("_", "-").lower()
    aliases = {"jp": "ja", "jpn": "ja", "eng": "en", "zh-cn": "zh-Hans", "zh-tw": "zh-Hant"}
    if lowered in aliases:
        return aliases[lowered]
    parts = lowered.split("-")
    if len(parts) > 1 and len(parts[1]) == 4:
        return f"{parts[0]}-{parts[1].title()}"
    return parts[0]


def prepare_batch(
    connection: sqlite3.Connection,
    batch: SourceBatch,
    topics: list[TopicConfig],
) -> tuple[int, int]:
    prepared = 0
    failed = 0
    for document in batch.documents:
        try:
            article_id = _prepare_document(connection, batch, document)
            for topic in topics:
                source_matches = batch.source.topic_specific or _matches_topic(document, topic)
                if source_matches and within_lookback(document, topic, batch.fetched_at):
                    connection.execute(
                        """INSERT OR IGNORE INTO topic_articles
                           (topic_id, topic_version, article_id, source_id, discovered_at)
                           VALUES (?, ?, ?, ?, ?)""",
                        (
                            topic.id,
                            topic.version,
                            article_id,
                            batch.source.id,
                            batch.fetched_at.isoformat(),
                        ),
                    )
            prepared += 1
        except Exception as error:
            connection.execute(
                """INSERT INTO source_documents
                   (fetch_id, source_url, fetched_at, response_metadata_json,
                    raw_metadata_json, extraction_status, failure_reason)
                   VALUES (?, ?, ?, ?, ?, 'preparation_failed', ?)""",
                (
                    batch.fetch_id,
                    document.source_url,
                    batch.fetched_at.isoformat(),
                    json.dumps(document.response_metadata or batch.response_metadata),
                    metadata_json(document),
                    str(error),
                ),
            )
            failed += 1
    return prepared, failed


def _prepare_document(
    connection: sqlite3.Connection,
    batch: SourceBatch,
    document: CollectedDocument,
) -> int:
    title = normalize_text(document.title)
    if not title:
        raise ValueError("missing normalized title")
    canonical_url = canonicalize_url(document.source_url)
    if not canonical_url.startswith(("http://", "https://")):
        raise ValueError("unsupported article URL")
    excerpt = normalize_text(document.excerpt)
    content = normalize_text(document.content) if batch.source.retain_content else None
    article_id = upsert_article(
        connection,
        canonical_url=canonical_url,
        stable_id=document.stable_id,
        source_id=batch.source.id,
        title=title,
        excerpt=excerpt,
        author=normalize_text(document.author),
        language=canonical_language(document.language),
        published_at=document.published_at,
        updated_at=document.updated_at,
        discovered_at=batch.fetched_at,
        fetched_at=batch.fetched_at,
        content=content,
    )
    for raw_discussion_url in document.discussion_urls:
        discussion_url = canonicalize_url(raw_discussion_url)
        if discussion_url.startswith(("http://", "https://")) and discussion_url != canonical_url:
            connection.execute(
                """INSERT OR IGNORE INTO article_links
                   (article_id, kind, source_id, url, discovered_at)
                   VALUES (?, 'discussion', ?, ?, ?)""",
                (article_id, batch.source.id, discussion_url, batch.fetched_at.isoformat()),
            )
    connection.execute(
        """INSERT INTO source_documents
           (fetch_id, article_id, source_url, fetched_at, response_metadata_json,
            raw_metadata_json, extracted_content, extraction_status)
           VALUES (?, ?, ?, ?, ?, ?, ?, 'prepared')""",
        (
            batch.fetch_id,
            article_id,
            document.source_url,
            batch.fetched_at.isoformat(),
            json.dumps(document.response_metadata or batch.response_metadata),
            metadata_json(document),
            content,
        ),
    )
    for market in batch.source.markets:
        connection.execute(
            """INSERT OR IGNORE INTO article_markets
               (article_id, market, confidence, evidence, classifier_version)
               VALUES (?, ?, 0.7, ?, 'source-market-v1')""",
            (article_id, market, f"published by source {batch.source.id}"),
        )
    searchable = f"{title} {excerpt or ''}"
    inferred_markets = {
        "CN": ("china", "chinese", "中国", "中國"),
        "JP": ("japan", "japanese", "日本"),
    }
    for market, terms in inferred_markets.items():
        matched = next((term for term in terms if term.lower() in searchable.lower()), None)
        if matched:
            connection.execute(
                """INSERT OR IGNORE INTO article_markets
                   (article_id, market, confidence, evidence, classifier_version)
                   VALUES (?, ?, 0.6, ?, 'lexical-market-v1')""",
                (article_id, market, f"matched market term {matched!r}"),
            )
    return article_id


def _matches_topic(document: CollectedDocument, topic: TopicConfig) -> bool:
    searchable = f"{document.title} {document.excerpt or ''}".casefold()
    for term in [*topic.terms, *topic.entities]:
        needle = term.casefold().strip()
        if not needle:
            continue
        if re.fullmatch(r"[a-z0-9]+", needle):
            if re.search(rf"\b{re.escape(needle)}\b", searchable):
                return True
        elif needle in searchable:
            return True
    return False
