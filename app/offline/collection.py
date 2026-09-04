from __future__ import annotations

import json
import sqlite3
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Protocol

import feedparser
import httpx

from app.config import SourceConfig, TopicConfig

MAX_RETRY_AFTER_SECONDS = 300.0


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    content: bytes
    headers: dict[str, str]


class HttpFetcher(Protocol):
    def get(self, url: str, headers: dict[str, str]) -> HttpResponse: ...


class HttpxFetcher:
    def __init__(self, timeout_seconds: float = 20) -> None:
        self.client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": "daily-news/0.1 (+local personal reader)"},
        )

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        response = self.client.get(url, headers=headers)
        return HttpResponse(response.status_code, response.content, dict(response.headers))

    def __enter__(self) -> HttpxFetcher:
        return self

    def __exit__(self, *args: object) -> None:
        self.client.close()


@dataclass(frozen=True)
class CollectedDocument:
    source_url: str
    title: str
    excerpt: str | None
    author: str | None
    language: str
    published_at: datetime | None
    updated_at: datetime | None
    content: str | None
    stable_id: str | None
    raw_metadata: dict[str, object]
    response_metadata: dict[str, str] | None = None
    discussion_urls: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceBatch:
    fetch_id: int
    source: SourceConfig
    fetched_at: datetime
    documents: list[CollectedDocument]
    response_metadata: dict[str, str]
    not_modified: bool = False
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourceFailure:
    source_id: str
    error: str


def _entry_date(entry: feedparser.FeedParserDict, field: str) -> datetime | None:
    parsed = entry.get(f"{field}_parsed")
    if parsed:
        return datetime(
            parsed.tm_year,
            parsed.tm_mon,
            parsed.tm_mday,
            parsed.tm_hour,
            parsed.tm_min,
            parsed.tm_sec,
            tzinfo=UTC,
        )
    value = entry.get(field)
    if isinstance(value, str):
        try:
            result = parsedate_to_datetime(value)
            return result.astimezone(UTC) if result.tzinfo else result.replace(tzinfo=UTC)
        except (TypeError, ValueError, OverflowError):
            return None
    return None


def parse_feed(content: bytes, source: SourceConfig) -> list[CollectedDocument]:
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"invalid feed: {feed.bozo_exception}")
    documents: list[CollectedDocument] = []
    for entry in feed.entries:
        link = str(entry.get("link") or "").strip()
        title = str(entry.get("title") or "").strip()
        if not link or not title:
            continue
        content_parts = entry.get("content") or []
        text = str(content_parts[0].get("value")) if content_parts else None
        stable_id = str(entry.get("id") or "").strip() or None
        if source.kind == "arxiv_api" and stable_id:
            stable_id = stable_id.replace("http://arxiv.org/abs/", "arxiv:").replace(
                "https://arxiv.org/abs/", "arxiv:"
            )
            stable_id = (
                stable_id.rsplit("v", 1)[0] if stable_id.rsplit("v", 1)[-1].isdigit() else stable_id
            )
        documents.append(
            CollectedDocument(
                source_url=link,
                title=title,
                excerpt=str(entry.get("summary") or "").strip() or None,
                author=str(entry.get("author") or "").strip() or None,
                language=str(entry.get("language") or source.languages[0]),
                published_at=_entry_date(entry, "published"),
                updated_at=_entry_date(entry, "updated"),
                content=text,
                stable_id=stable_id,
                raw_metadata={
                    "id": entry.get("id"),
                    "title": entry.get("title"),
                    "link": entry.get("link"),
                    "published": entry.get("published"),
                    "updated": entry.get("updated"),
                    "author": entry.get("author"),
                    "language": entry.get("language"),
                    "summary": entry.get("summary"),
                    "content": entry.get("content"),
                },
            )
        )
    return documents


def parse_hacker_news_item(content: bytes, source: SourceConfig) -> CollectedDocument | None:
    try:
        item = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid Hacker News item JSON: {error}") from error
    if not isinstance(item, dict):
        raise ValueError("Hacker News item must be a JSON object")
    if item.get("deleted") or item.get("dead") or item.get("type") != "story":
        return None
    item_id = item.get("id")
    title = item.get("title")
    created = item.get("time")
    if not isinstance(item_id, int) or not isinstance(title, str) or not isinstance(created, int):
        raise ValueError("Hacker News story is missing id, title, or time")
    discussion_url = f"https://news.ycombinator.com/item?id={item_id}"
    outbound_url = item.get("url")
    source_url = outbound_url if isinstance(outbound_url, str) and outbound_url else discussion_url
    text = item.get("text")
    content_text = text if isinstance(text, str) and text else None
    return CollectedDocument(
        source_url=source_url,
        title=title,
        excerpt=content_text,
        author=str(item["by"]) if item.get("by") else None,
        language=source.languages[0],
        published_at=datetime.fromtimestamp(created, tz=UTC),
        updated_at=None,
        content=content_text,
        stable_id=f"hackernews:{item_id}",
        raw_metadata=dict(item) | {"discussion_url": discussion_url},
        discussion_urls=(discussion_url,),
    )


class Collector:
    def __init__(
        self,
        fetcher: HttpFetcher,
        sleep: Callable[[float], None] = time.sleep,
        on_retry: Callable[[str, str, float, int, int], None] | None = None,
    ) -> None:
        self.fetcher = fetcher
        self.sleep = sleep
        self.on_retry = on_retry

    def collect_source(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        source: SourceConfig,
        now: datetime,
    ) -> SourceBatch:
        prior = connection.execute(
            """SELECT etag, last_modified FROM source_fetches
               WHERE source_id=? AND status='success' ORDER BY id DESC LIMIT 1""",
            (source.id,),
        ).fetchone()
        headers: dict[str, str] = {}
        if prior and prior["etag"]:
            headers["If-None-Match"] = prior["etag"]
        if prior and prior["last_modified"]:
            headers["If-Modified-Since"] = prior["last_modified"]
        cursor = connection.execute(
            """INSERT INTO source_fetches(run_id, source_id, started_at, status)
               VALUES (?, ?, ?, 'running')""",
            (run_id, source.id, now.isoformat()),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("source fetch insert did not return an ID")
        fetch_id = cursor.lastrowid
        try:
            request_url = source.url
            if source.kind == "bluesky_search":
                from app.offline.bluesky import search_url

                request_url = search_url(source, limit=min(100, source.max_items))
            response = self._request(source, request_url, headers)
            if response.status_code == 304:
                etag = prior["etag"] if prior else None
                last_modified = prior["last_modified"] if prior else None
                connection.execute(
                    """UPDATE source_fetches SET finished_at=?, status='success',
                       http_status=304, etag=?, last_modified=? WHERE id=?""",
                    (now.isoformat(), etag, last_modified, fetch_id),
                )
                return SourceBatch(fetch_id, source, now, [], response.headers, True)
            if source.kind == "hackernews_api":
                documents, warnings = self._collect_hacker_news(source, response.content)
            elif source.kind == "bluesky_search":
                from app.offline.bluesky import collect_search
                from app.offline.link_resolution import resolve_aggregator_links

                documents, warnings = collect_search(
                    source,
                    response,
                    self._request,
                    sleep=self.sleep,
                )
                documents, resolution_warnings = resolve_aggregator_links(
                    documents,
                    lambda url: self._request(source, url),
                )
                warnings.extend(resolution_warnings)
            elif source.kind in {"rss", "atom", "arxiv_api"}:
                documents, warnings = parse_feed(response.content, source), []
            else:
                raise ValueError(f"source adapter is not implemented for kind {source.kind!r}")
            status = "partial" if warnings else "success"
            error = "; ".join(warnings) if warnings else None
            connection.execute(
                """UPDATE source_fetches SET finished_at=?, status=?, http_status=?,
                   etag=?, last_modified=?, error=? WHERE id=?""",
                (
                    now.isoformat(),
                    status,
                    response.status_code,
                    response.headers.get("etag"),
                    response.headers.get("last-modified"),
                    error,
                    fetch_id,
                ),
            )
            return SourceBatch(
                fetch_id, source, now, documents, response.headers, warnings=tuple(warnings)
            )
        except Exception as error:
            connection.execute(
                "UPDATE source_fetches SET finished_at=?, status='failed', error=? WHERE id=?",
                (now.isoformat(), str(error), fetch_id),
            )
            raise RuntimeError(str(error)) from error

    def _request(
        self, source: SourceConfig, url: str, headers: dict[str, str] | None = None
    ) -> HttpResponse:
        last_error: Exception | None = None
        for attempt in range(source.retry.attempts):
            retry_after = 0.0
            try:
                response = self.fetcher.get(url, headers or {})
                if response.status_code in {429, 500, 502, 503, 504}:
                    retry_after = _retry_after_seconds(response.headers)
                    raise RuntimeError(f"retryable HTTP status {response.status_code} for {url}")
                if response.status_code >= 400:
                    raise RuntimeError(f"HTTP status {response.status_code} for {url}")
                return response
            except Exception as error:
                last_error = error
                if attempt + 1 < source.retry.attempts:
                    delay = max(
                        source.rate_limit_seconds,
                        source.retry.backoff_seconds * (2**attempt),
                        retry_after,
                    )
                    if self.on_retry is not None:
                        self.on_retry(
                            source.name,
                            str(error),
                            delay,
                            attempt + 2,
                            source.retry.attempts,
                        )
                    self.sleep(delay)
        raise RuntimeError(str(last_error))

    def _collect_hacker_news(
        self, source: SourceConfig, content: bytes
    ) -> tuple[list[CollectedDocument], list[str]]:
        try:
            raw_ids = json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid Hacker News story-list JSON: {error}") from error
        if not isinstance(raw_ids, list) or not all(isinstance(item, int) for item in raw_ids):
            raise ValueError("Hacker News story-list response must be an array of integer IDs")
        base_url = source.url.rsplit("/", 1)[0]
        documents: list[CollectedDocument] = []
        warnings: list[str] = []
        for index, item_id in enumerate(raw_ids[: source.max_items]):
            if index and source.rate_limit_seconds:
                self.sleep(source.rate_limit_seconds)
            item_url = f"{base_url}/item/{item_id}.json"
            try:
                response = self._request(source, item_url)
                document = parse_hacker_news_item(response.content, source)
                if document is not None:
                    documents.append(replace(document, response_metadata=response.headers))
            except Exception as error:
                warnings.append(f"item {item_id}: {error}")
        return documents, warnings


def begin_run(connection: sqlite3.Connection, topic_id: str | None, now: datetime) -> str:
    run_id = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO collection_runs(id, started_at, topic_id, status) VALUES (?, ?, ?, 'running')",
        (run_id, now.isoformat(), topic_id),
    )
    return run_id


def finish_run(connection: sqlite3.Connection, run_id: str, now: datetime, failed: bool) -> None:
    connection.execute(
        "UPDATE collection_runs SET finished_at=?, status=? WHERE id=?",
        (now.isoformat(), "partial" if failed else "success", run_id),
    )


def within_lookback(document: CollectedDocument, topic: TopicConfig, now: datetime) -> bool:
    relevant_time = document.published_at or document.updated_at or now
    return relevant_time >= now - timedelta(hours=topic.freshness_hours)


def metadata_json(document: CollectedDocument) -> str:
    return json.dumps(document.raw_metadata, ensure_ascii=False, default=str)


def _retry_after_seconds(headers: dict[str, str], now: datetime | None = None) -> float:
    raw = next(
        (value for key, value in headers.items() if key.lower() == "retry-after"),
        None,
    )
    if not raw:
        return 0.0
    try:
        seconds = float(raw)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(raw)
            if retry_at.tzinfo is None:
                retry_at = retry_at.replace(tzinfo=UTC)
            seconds = (retry_at - (now or datetime.now(UTC))).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return 0.0
    return min(MAX_RETRY_AFTER_SECONDS, max(0.0, seconds))
