from __future__ import annotations

import hashlib
import ipaddress
import json
import socket
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx
from trafilatura import extract

from app.llm.runtime import InferenceError, TextInference

SUMMARY_PROMPT_VERSION = "short-factual-summary-v4"
MAX_HTML_BYTES = 3_000_000
MIN_ARTICLE_CHARACTERS = 200
USER_AGENT = "daily-news/0.1 (+local personal reader)"


class SummaryUnavailable(RuntimeError):
    """Raised when an article cannot be safely fetched, extracted, or summarized."""


@dataclass(frozen=True)
class FetchedPage:
    requested_url: str
    final_url: str
    status_code: int
    headers: dict[str, str]
    html: str


class ArticlePageFetcher(Protocol):
    def fetch(self, url: str) -> FetchedPage: ...


class HttpxArticlePageFetcher:
    def __init__(
        self,
        timeout_seconds: float = 20,
        max_bytes: int = MAX_HTML_BYTES,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.max_bytes = max_bytes
        self.client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": USER_AGENT},
            event_hooks={"request": [self._validate_request]},
            transport=transport,
        )

    def __enter__(self) -> HttpxArticlePageFetcher:
        return self

    def __exit__(self, *args: object) -> None:
        self.client.close()

    def fetch(self, url: str) -> FetchedPage:
        _require_public_http_url(url)
        if not self._robots_allows(url):
            raise SummaryUnavailable("article fetch is disallowed by the site's robots policy")
        try:
            with self.client.stream("GET", url) as response:
                response.raise_for_status()
                final_url = str(response.url)
                _require_public_http_url(final_url)
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type and "application/xhtml+xml" not in content_type:
                    raise SummaryUnavailable(
                        f"article returned unsupported content type {content_type!r}"
                    )
                chunks: list[bytes] = []
                size = 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise SummaryUnavailable(
                            f"article HTML exceeds the {self.max_bytes:,}-byte limit"
                        )
                    chunks.append(chunk)
                encoding = response.encoding or "utf-8"
                html = b"".join(chunks).decode(encoding, errors="replace")
                return FetchedPage(
                    requested_url=url,
                    final_url=final_url,
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    html=html,
                )
        except SummaryUnavailable:
            raise
        except httpx.HTTPError as error:
            raise SummaryUnavailable(f"article fetch failed: {error}") from error

    def _validate_request(self, request: httpx.Request) -> None:
        _require_public_http_url(str(request.url))

    def _robots_allows(self, url: str) -> bool:
        parts = urlsplit(url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        try:
            response = self.client.get(robots_url)
        except httpx.HTTPError as error:
            raise SummaryUnavailable(f"robots policy could not be checked: {error}") from error
        if response.status_code == 404:
            return True
        if response.status_code >= 400:
            raise SummaryUnavailable(
                f"robots policy could not be checked: HTTP {response.status_code}"
            )
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        return parser.can_fetch(USER_AGENT, url)


@dataclass(frozen=True)
class SummaryResult:
    text: str
    cached: bool


def get_or_create_summary(
    connection: sqlite3.Connection,
    *,
    article_id: int,
    url: str,
    inference: TextInference | None,
    fetcher: ArticlePageFetcher | None,
    retain_content: bool,
    progress: Callable[[str], None] | None = None,
) -> SummaryResult:
    report = progress or (lambda _: None)
    if inference is None:
        raise SummaryUnavailable(
            "local summarization is not configured; set llm.model in settings.yaml first"
        )
    report("Checking the local summary cache…")
    cached = connection.execute(
        """SELECT summary_text FROM article_summaries
           WHERE article_id=? AND target_language='en' AND model_version=?
           AND prompt_version=? ORDER BY id DESC LIMIT 1""",
        (article_id, inference.model_version, SUMMARY_PROMPT_VERSION),
    ).fetchone()
    if cached:
        report("Found a cached summary.")
        return SummaryResult(str(cached["summary_text"]), True)
    if fetcher is None:
        raise SummaryUnavailable("article HTML fetcher is unavailable")

    report("Checking robots policy and fetching article HTML…")
    page = fetcher.fetch(url)
    report("Extracting the article's main text…")
    try:
        extracted = extract(
            page.html,
            url=page.final_url,
            include_comments=False,
            include_tables=False,
            output_format="txt",
        )
    except Exception as error:
        _record_content_version(connection, article_id, page, "failed", None, str(error))
        raise SummaryUnavailable(f"article extraction failed: {error}") from error
    text = extracted.strip() if extracted else ""
    if len(text) < MIN_ARTICLE_CHARACTERS:
        reason = f"extracted article text is too short ({len(text)} characters)"
        _record_content_version(connection, article_id, page, "failed", None, reason)
        raise SummaryUnavailable(reason)

    content_version_id, content_hash = _record_content_version(
        connection,
        article_id,
        page,
        "extracted",
        text if retain_content else None,
        None,
        hash_text=text,
    )
    report("Loading AI model…")
    try:
        inference.prepare()
        report("Generating summary…")
        summary = inference.summarize(text, "en").strip()
    except Exception as error:
        _record_processing_outcome(
            connection, article_id, "failed", str(error), inference.model_version
        )
        if isinstance(error, InferenceError):
            raise
        raise InferenceError(f"local summarization failed: {error}") from error
    if not summary:
        _record_processing_outcome(
            connection,
            article_id,
            "failed",
            "local model returned an empty summary",
            inference.model_version,
        )
        raise SummaryUnavailable("local model returned an empty summary")
    connection.execute(
        """INSERT OR IGNORE INTO article_summaries
           (article_id, content_version_id, input_hash, target_language, model_version,
            prompt_version, summary_text, created_at)
           VALUES (?, ?, ?, 'en', ?, ?, ?, ?)""",
        (
            article_id,
            content_version_id,
            content_hash,
            inference.model_version,
            SUMMARY_PROMPT_VERSION,
            summary,
            datetime.now(UTC).isoformat(),
        ),
    )
    if retain_content:
        connection.execute("UPDATE articles SET content=? WHERE id=?", (text, article_id))
    report("Saved the extracted content and summary locally.")
    return SummaryResult(summary, False)


def _record_processing_outcome(
    connection: sqlite3.Connection,
    article_id: int,
    status: str,
    reason: str,
    processor_version: str,
) -> None:
    connection.execute(
        """INSERT INTO processing_outcomes
           (article_id, stage, field, status, reason, processor_version, created_at)
           VALUES (?, 'summary', NULL, ?, ?, ?, ?)""",
        (
            article_id,
            status,
            reason,
            processor_version,
            datetime.now(UTC).isoformat(),
        ),
    )


def _record_content_version(
    connection: sqlite3.Connection,
    article_id: int,
    page: FetchedPage,
    status: str,
    extracted_text: str | None,
    failure_reason: str | None,
    *,
    hash_text: str | None = None,
) -> tuple[int, str]:
    content_hash = hashlib.sha256((hash_text or page.html).encode()).hexdigest()
    cursor = connection.execute(
        """INSERT INTO article_content_versions
           (article_id, requested_url, final_url, fetched_at, http_status,
            response_headers_json, content_hash, extracted_text, extraction_status,
            failure_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            article_id,
            page.requested_url,
            page.final_url,
            datetime.now(UTC).isoformat(),
            page.status_code,
            json.dumps(page.headers),
            content_hash,
            extracted_text,
            status,
            failure_reason,
        ),
    )
    if cursor.lastrowid is None:
        raise RuntimeError("content-version insert did not return an ID")
    return cursor.lastrowid, content_hash


def _require_public_http_url(url: str) -> None:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise SummaryUnavailable("article URL must be public HTTP(S) without credentials")
    hostname = parts.hostname.rstrip(".").lower()
    if hostname == "localhost" or hostname.endswith((".localhost", ".local")):
        raise SummaryUnavailable("article URL resolves to a local host")
    try:
        default_port = 80 if parts.scheme == "http" else 443
        addresses = {
            item[4][0] for item in socket.getaddrinfo(hostname, parts.port or default_port)
        }
    except OSError as error:
        raise SummaryUnavailable(f"article host could not be resolved: {error}") from error
    for address in addresses:
        parsed = ipaddress.ip_address(address)
        if not parsed.is_global:
            raise SummaryUnavailable("article URL resolves to a non-public address")
