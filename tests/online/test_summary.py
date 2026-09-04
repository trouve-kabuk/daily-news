from __future__ import annotations

import httpx
import pytest

from app.online.summary import HttpxArticlePageFetcher, SummaryUnavailable


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "http://localhost/article",
        "http://127.0.0.1/article",
        "http://user:secret@example.com/article",
    ],
)
def test_html_fetcher_rejects_non_public_or_credentialed_urls(url: str) -> None:
    with HttpxArticlePageFetcher() as fetcher, pytest.raises(SummaryUnavailable):
        fetcher.fetch(url)


def test_html_fetcher_honors_robots_and_content_limit() -> None:
    def allowed_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /", request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html; charset=utf-8"},
            content=b"<html><body><article>bounded article</article></body></html>",
            request=request,
        )

    with HttpxArticlePageFetcher(
        max_bytes=1000, transport=httpx.MockTransport(allowed_handler)
    ) as fetcher:
        page = fetcher.fetch("https://93.184.216.34/article")
    assert "bounded article" in page.html

    def denied_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="User-agent: *\nDisallow: /", request=request)

    with (
        HttpxArticlePageFetcher(transport=httpx.MockTransport(denied_handler)) as fetcher,
        pytest.raises(SummaryUnavailable, match="robots policy"),
    ):
        fetcher.fetch("https://93.184.216.34/article")

    def oversized_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404, request=request)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            content=b"x" * 101,
            request=request,
        )

    with (
        HttpxArticlePageFetcher(
            max_bytes=100, transport=httpx.MockTransport(oversized_handler)
        ) as fetcher,
        pytest.raises(SummaryUnavailable, match="exceeds"),
    ):
        fetcher.fetch("https://93.184.216.34/article")
