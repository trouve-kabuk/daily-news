from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

from app.config import Settings
from app.knowledge.store import Store
from app.offline.collection import HttpResponse
from app.offline.pipeline import collect_topics


class BlueskyFetcher:
    def __init__(self, pages: list[dict[str, object]]) -> None:
        self.pages = pages
        self.urls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.urls.append(url)
        return HttpResponse(
            200,
            json.dumps(self.pages.pop(0)).encode(),
            {"x-request-id": f"request-{len(self.urls)}"},
        )


class BlueskyHackerNewsFetcher:
    def __init__(self, page: dict[str, object], hacker_news_item: dict[str, object]) -> None:
        self.page = page
        self.hacker_news_item = hacker_news_item
        self.urls: list[str] = []

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        self.urls.append(url)
        payload = self.hacker_news_item if "hacker-news.firebaseio.com" in url else self.page
        return HttpResponse(200, json.dumps(payload).encode(), {})


def _post(
    key: str,
    *,
    text: str,
    external_url: str | None,
    external_title: str = "",
) -> dict[str, object]:
    post: dict[str, object] = {
        "uri": f"at://did:plc:alice/app.bsky.feed.post/{key}",
        "cid": f"cid-{key}",
        "author": {
            "did": "did:plc:alice",
            "handle": "alice.example",
            "displayName": "Alice",
        },
        "record": {
            "$type": "app.bsky.feed.post",
            "text": text,
            "createdAt": "2026-09-04T01:02:03.000Z",
            "langs": ["en"],
        },
        "indexedAt": "2026-09-04T01:02:04.000Z",
    }
    if external_url:
        post["embed"] = {
            "$type": "app.bsky.embed.external#view",
            "external": {
                "uri": external_url,
                "title": external_title,
                "description": "External description",
            },
        }
    return post


def test_bluesky_collects_linked_articles_and_retains_each_post(
    settings: Settings, store: Store
) -> None:
    source = settings.sources["bluesky-ai-engineering"].model_copy(
        update={"max_items": 3, "rate_limit_seconds": 0}
    )
    settings.sources[source.id] = source
    topic = settings.topics["ai-engineering"].model_copy(update={"sources": [source.id]})
    fetcher = BlueskyFetcher(
        [
            {
                "posts": [
                    _post(
                        "one",
                        text="A substantial new coding agent release.",
                        external_url="https://example.com/agent?utm_source=bluesky",
                        external_title="New coding agent",
                    ),
                    _post(
                        "two",
                        text="More details about this coding agent.",
                        external_url="https://example.com/agent",
                        external_title="New coding agent",
                    ),
                    _post(
                        "linkless",
                        text="A linkless LLM observation.",
                        external_url=None,
                    ),
                ]
            }
        ]
    )

    with store.transaction() as connection:
        report = collect_topics(
            connection,
            settings,
            [topic],
            fetcher,
            now=datetime(2026, 9, 4, 2, tzinfo=UTC),
            sleep=lambda _: None,
        )
        article = connection.execute("SELECT * FROM articles").fetchone()
        documents = connection.execute("SELECT * FROM source_documents ORDER BY id").fetchall()
        links = connection.execute("SELECT url FROM article_links ORDER BY url").fetchall()

    assert report.articles_prepared == 2
    assert report.failures == []
    assert article["canonical_url"] == "https://example.com/agent"
    assert article["stable_id"] is None
    assert len(documents) == 2
    assert json.loads(documents[0]["raw_metadata_json"])["uri"].endswith("/one")
    assert [row["url"] for row in links] == [
        "https://bsky.app/profile/alice.example/post/one",
        "https://bsky.app/profile/alice.example/post/two",
    ]
    query = parse_qs(urlsplit(fetcher.urls[0]).query)
    assert query["sort"] == ["latest"]
    assert query["lang"] == ["en"]
    assert query["limit"] == ["3"]


def test_bluesky_paginates_within_one_collect_run_and_reports_bad_posts(
    settings: Settings, store: Store
) -> None:
    source = settings.sources["bluesky-ai-engineering"].model_copy(
        update={"max_items": 3, "rate_limit_seconds": 0}
    )
    settings.sources[source.id] = source
    topic = settings.topics["ai-engineering"].model_copy(update={"sources": [source.id]})
    fetcher = BlueskyFetcher(
        [
            {
                "cursor": "next-page",
                "posts": [
                    _post(
                        "one",
                        text="A new model API is available.",
                        external_url="https://example.com/model-api",
                        external_title="Model API release",
                    )
                ],
            },
            {
                "posts": [
                    {"uri": "not-an-at-uri"},
                    _post(
                        "two",
                        text="An LLM developer tools release.",
                        external_url="https://example.com/tools",
                        external_title="LLM developer tools",
                    ),
                ]
            },
        ]
    )

    with store.transaction() as connection:
        report = collect_topics(
            connection,
            settings,
            [topic],
            fetcher,
            now=datetime(2026, 9, 4, 2, tzinfo=UTC),
            sleep=lambda _: None,
        )
        status = connection.execute(
            "SELECT status FROM source_fetches WHERE source_id=?", (source.id,)
        ).fetchone()[0]
        article_count = connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0]

    assert article_count == 2
    assert report.articles_prepared == 2
    assert len(report.failures) == 1
    assert "missing valid AT URI" in report.failures[0].error
    assert status == "partial"
    assert parse_qs(urlsplit(fetcher.urls[1]).query)["cursor"] == ["next-page"]


def test_bluesky_hacker_news_link_resolves_to_article_and_keeps_both_discussions(
    settings: Settings, store: Store
) -> None:
    source = settings.sources["bluesky-ai-engineering"].model_copy(
        update={"max_items": 1, "rate_limit_seconds": 0}
    )
    settings.sources[source.id] = source
    topic = settings.topics["ai-engineering"].model_copy(update={"sources": [source.id]})
    fetcher = BlueskyHackerNewsFetcher(
        {
            "posts": [
                _post(
                    "hn-link",
                    text="This coding agent launch is worth discussing.",
                    external_url="https://news.ycombinator.com/item?id=4242",
                    external_title="Discussion on Hacker News",
                )
            ]
        },
        {
            "id": 4242,
            "type": "story",
            "by": "submitter",
            "time": 1_788_400_000,
            "title": "A new coding agent",
            "url": "https://example.com/coding-agent?utm_source=hackernews",
        },
    )

    with store.transaction() as connection:
        report = collect_topics(
            connection,
            settings,
            [topic],
            fetcher,
            now=datetime(2026, 9, 4, 2, tzinfo=UTC),
            sleep=lambda _: None,
        )
        article = connection.execute("SELECT * FROM articles").fetchone()
        links = connection.execute("SELECT url FROM article_links ORDER BY url").fetchall()
        raw_metadata = connection.execute(
            "SELECT raw_metadata_json FROM source_documents"
        ).fetchone()[0]

    assert report.failures == []
    assert article["canonical_url"] == "https://example.com/coding-agent"
    assert article["title"] == "A new coding agent"
    assert [row["url"] for row in links] == [
        "https://bsky.app/profile/alice.example/post/hn-link",
        "https://news.ycombinator.com/item?id=4242",
    ]
    metadata = json.loads(raw_metadata)
    assert metadata["link_resolutions"][0]["resolver"] == "hackernews"
    assert fetcher.urls[1] == "https://hacker-news.firebaseio.com/v0/item/4242.json"
