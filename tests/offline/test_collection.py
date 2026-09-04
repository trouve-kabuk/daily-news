from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from app.config import Settings, SourceConfig
from app.knowledge.store import Store
from app.offline.collection import HttpResponse, _retry_after_seconds
from app.offline.pipeline import collect_topics
from app.offline.preparation import canonical_language, canonicalize_url


class FixtureFetcher:
    def __init__(self, responses: dict[str, list[HttpResponse]]) -> None:
        self.responses = responses
        self.headers: list[dict[str, str]] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.headers.append(headers)
        return self.responses[url].pop(0)


def test_collection_retries_lookback_normalizes_and_deduplicates(
    settings: Settings, store: Store
) -> None:
    topic = settings.topics["ai-engineering"]
    source = SourceConfig.model_validate(
        settings.sources["openai-news"].model_dump()
        | {"retry": {"attempts": 2, "backoff_seconds": 0}, "rate_limit_seconds": 0}
    )
    settings.sources["openai-news"] = source
    topic = topic.model_copy(update={"sources": ["openai-news"]})
    body = Path("tests/fixtures/engineering.xml").read_bytes()
    fetcher = FixtureFetcher(
        {
            source.url: [
                HttpResponse(503, b"", {}),
                HttpResponse(200, body, {"etag": '"one"'}),
                HttpResponse(200, body, {"etag": '"two"'}),
            ]
        }
    )
    now = datetime(2026, 9, 3, 2, tzinfo=UTC)
    with store.transaction() as connection:
        first = collect_topics(
            connection, settings, [topic], fetcher, now=now, sleep=lambda _: None
        )
        second = collect_topics(
            connection, settings, [topic], fetcher, now=now, sleep=lambda _: None
        )
        assert first.articles_prepared == 2
        assert second.articles_prepared == 2
        assert connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 2
        assert connection.execute("SELECT COUNT(*) FROM topic_articles").fetchone()[0] == 1
        row = connection.execute("SELECT * FROM articles WHERE title LIKE 'New%'").fetchone()
        assert row["canonical_url"] == "https://example.com/agent"
        assert row["excerpt"] == "A substantial tool for developers."
    assert fetcher.headers[-1]["If-None-Match"] == '"one"'


def test_source_failure_does_not_abort_other_source(settings: Settings, store: Store) -> None:
    topic = settings.topics["ai-papers"].model_copy(
        update={"sources": ["arxiv-cs-ai", "failing-feed"]}
    )
    arxiv = SourceConfig.model_validate(
        settings.sources["arxiv-cs-ai"].model_dump()
        | {"retry": {"attempts": 1}, "rate_limit_seconds": 0}
    )
    failing = SourceConfig.model_validate(
        arxiv.model_dump()
        | {
            "id": "failing-feed",
            "name": "Failing feed",
            "kind": "rss",
            "url": "https://example.com/failing.xml",
            "retry": {"attempts": 1},
            "rate_limit_seconds": 0,
        }
    )
    settings.sources[arxiv.id] = arxiv
    settings.sources[failing.id] = failing
    fetcher = FixtureFetcher(
        {
            arxiv.url: [HttpResponse(200, Path("tests/fixtures/papers.xml").read_bytes(), {})],
            failing.url: [HttpResponse(500, b"", {})],
        }
    )
    started_sources: list[str] = []
    with store.transaction() as connection:
        report = collect_topics(
            connection,
            settings,
            [topic],
            fetcher,
            now=datetime(2026, 9, 3, 2, tzinfo=UTC),
            sleep=lambda _: None,
            on_source_start=started_sources.append,
        )
        article = connection.execute("SELECT * FROM articles").fetchone()
    assert report.sources_succeeded == 1
    assert [failure.source_id for failure in report.failures] == ["failing-feed"]
    assert started_sources == ["arXiv cs.AI", "Failing feed"]
    assert article["stable_id"] == "arxiv:2609.00001"


def test_arxiv_429_honors_retry_after(settings: Settings, store: Store) -> None:
    source = SourceConfig.model_validate(
        settings.sources["arxiv-cs-ai"].model_dump()
        | {"retry": {"attempts": 2, "backoff_seconds": 1}}
    )
    settings.sources[source.id] = source
    topic = settings.topics["ai-papers"].model_copy(update={"sources": [source.id]})
    sleeps: list[float] = []
    retries: list[tuple[str, str, float, int, int]] = []
    fetcher = FixtureFetcher(
        {
            source.url: [
                HttpResponse(429, b"", {"Retry-After": "7"}),
                HttpResponse(200, Path("tests/fixtures/papers.xml").read_bytes(), {}),
            ]
        }
    )

    with store.transaction() as connection:
        report = collect_topics(
            connection,
            settings,
            [topic],
            fetcher,
            now=datetime(2026, 9, 4, 2, tzinfo=UTC),
            sleep=sleeps.append,
            on_retry=lambda *notice: retries.append(notice),
        )

    assert report.sources_succeeded == 1
    assert sleeps == [7.0]
    assert retries == [
        (
            "arXiv cs.AI",
            f"retryable HTTP status 429 for {source.url}",
            7.0,
            2,
            2,
        )
    ]


def test_retry_after_parses_http_date_and_is_bounded() -> None:
    now = datetime(2026, 9, 4, 2, tzinfo=UTC)
    assert _retry_after_seconds({"retry-after": "Fri, 04 Sep 2026 02:00:12 GMT"}, now) == 12
    assert _retry_after_seconds({"Retry-After": "9999"}, now) == 300
    assert _retry_after_seconds({"Retry-After": "not-a-delay"}, now) == 0


def test_canonical_helpers() -> None:
    assert (
        canonicalize_url("HTTPS://Example.COM:443/x/?utm_medium=a&b=2#part")
        == "https://example.com/x?b=2"
    )
    assert canonical_language("zh_CN") == "zh-Hans"
    assert canonical_language("zh-Hant") == "zh-Hant"


def test_hacker_news_api_collects_stories_and_filters_generic_items(
    settings: Settings, store: Store
) -> None:
    source = settings.sources["hacker-news"].model_copy(update={"max_items": 3})
    topic = settings.topics["ai-engineering"].model_copy(update={"sources": [source.id]})
    base = source.url.rsplit("/", 1)[0]
    fetcher = FixtureFetcher(
        {
            source.url: [HttpResponse(200, json.dumps([101, 102, 103, 104]).encode(), {})],
            f"{base}/item/101.json": [
                HttpResponse(
                    200,
                    json.dumps(
                        {
                            "id": 101,
                            "type": "story",
                            "by": "alice",
                            "time": 1_788_400_000,
                            "title": "A new AI coding agent",
                            "url": "https://example.com/ai-agent?utm_source=hn",
                            "score": 120,
                        }
                    ).encode(),
                    {"etag": "item-101"},
                )
            ],
            f"{base}/item/102.json": [
                HttpResponse(
                    200,
                    json.dumps({"id": 102, "type": "story", "dead": True}).encode(),
                    {},
                )
            ],
            f"{base}/item/103.json": [
                HttpResponse(
                    200,
                    json.dumps(
                        {
                            "id": 103,
                            "type": "story",
                            "by": "bob",
                            "time": 1_788_400_100,
                            "title": "Ask HN: Favorite keyboards?",
                            "text": "Tell me about your setup.",
                        }
                    ).encode(),
                    {},
                )
            ],
        }
    )
    with store.transaction() as connection:
        report = collect_topics(
            connection,
            settings,
            [topic],
            fetcher,
            now=datetime(2026, 9, 3, 2, tzinfo=UTC),
            sleep=lambda _: None,
        )
        assert report.articles_prepared == 2
        assert connection.execute("SELECT COUNT(*) FROM topic_articles").fetchone()[0] == 1
        ai_story = connection.execute(
            "SELECT * FROM articles WHERE stable_id='hackernews:101'"
        ).fetchone()
        ask_story = connection.execute(
            "SELECT * FROM articles WHERE stable_id='hackernews:103'"
        ).fetchone()
        metadata = connection.execute(
            """SELECT response_metadata_json FROM source_documents
               WHERE article_id=?""",
            (ai_story["id"],),
        ).fetchone()[0]
        discussion_url = connection.execute(
            "SELECT url FROM article_links WHERE article_id=? AND kind='discussion'",
            (ai_story["id"],),
        ).fetchone()[0]
    assert ai_story["canonical_url"] == "https://example.com/ai-agent"
    assert discussion_url == "https://news.ycombinator.com/item?id=101"
    assert ask_story["canonical_url"] == "https://news.ycombinator.com/item?id=103"
    assert json.loads(metadata)["etag"] == "item-101"


def test_hacker_news_item_failure_is_partial_not_fatal(settings: Settings, store: Store) -> None:
    source = SourceConfig.model_validate(
        settings.sources["hacker-news"].model_dump()
        | {"max_items": 2, "retry": {"attempts": 2, "backoff_seconds": 0}}
    )
    topic = settings.topics["ai-engineering"].model_copy(update={"sources": [source.id]})
    base = source.url.rsplit("/", 1)[0]
    valid = json.dumps(
        {
            "id": 201,
            "type": "story",
            "by": "carol",
            "time": 1_788_400_000,
            "title": "LLM tooling release",
            "url": "https://example.com/llm",
        }
    ).encode()
    fetcher = FixtureFetcher(
        {
            source.url: [HttpResponse(200, b"[201, 202]", {})],
            f"{base}/item/201.json": [HttpResponse(503, b"", {}), HttpResponse(200, valid, {})],
            f"{base}/item/202.json": [HttpResponse(500, b"", {}), HttpResponse(500, b"", {})],
        }
    )
    with store.transaction() as connection:
        report = collect_topics(
            connection,
            settings,
            [topic],
            fetcher,
            now=datetime(2026, 9, 3, 2, tzinfo=UTC),
            sleep=lambda _: None,
        )
        status = connection.execute(
            "SELECT status FROM source_fetches WHERE source_id='hacker-news'"
        ).fetchone()[0]
    assert report.sources_succeeded == 1
    assert report.articles_prepared == 1
    assert len(report.failures) == 1 and "item 202" in report.failures[0].error
    assert status == "partial"
