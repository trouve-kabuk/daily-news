from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol, cast
from urllib.parse import parse_qs, urlsplit

if TYPE_CHECKING:
    from app.offline.collection import CollectedDocument, HttpResponse


@dataclass(frozen=True)
class LinkResolution:
    resolver: str
    source_url: str
    canonical_url: str
    discussion_url: str
    title: str | None
    raw_metadata: dict[str, object]


class AggregatorResolver(Protocol):
    name: str

    def matches(self, url: str) -> bool: ...

    def resolve(
        self, url: str, request: Callable[[str], HttpResponse]
    ) -> LinkResolution | None: ...


class HackerNewsResolver:
    name = "hackernews"

    def matches(self, url: str) -> bool:
        parts = urlsplit(url)
        return (
            (parts.hostname or "").lower() in {"news.ycombinator.com", "www.news.ycombinator.com"}
            and parts.path.rstrip("/") == "/item"
            and _hacker_news_item_id(url) is not None
        )

    def resolve(self, url: str, request: Callable[[str], HttpResponse]) -> LinkResolution | None:
        item_id = _hacker_news_item_id(url)
        if item_id is None:
            return None
        response = request(f"https://hacker-news.firebaseio.com/v0/item/{item_id}.json")
        try:
            payload = json.loads(response.content)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError(f"invalid Hacker News item JSON: {error}") from error
        if not isinstance(payload, dict):
            raise ValueError("Hacker News item must be a JSON object")
        item = cast(dict[str, object], payload)
        if item.get("deleted") or item.get("dead") or item.get("type") != "story":
            return None
        if item.get("id") != item_id:
            raise ValueError("Hacker News response has an unexpected item ID")
        outbound_url = item.get("url")
        if not isinstance(outbound_url, str) or not _is_http_url(outbound_url):
            return None
        title = item.get("title")
        return LinkResolution(
            resolver=self.name,
            source_url=url,
            canonical_url=outbound_url,
            discussion_url=f"https://news.ycombinator.com/item?id={item_id}",
            title=title if isinstance(title, str) and title.strip() else None,
            raw_metadata=item,
        )


RESOLVERS: tuple[AggregatorResolver, ...] = (HackerNewsResolver(),)


def resolve_aggregator_links(
    documents: list[CollectedDocument],
    request: Callable[[str], HttpResponse],
    *,
    resolvers: tuple[AggregatorResolver, ...] = RESOLVERS,
    max_hops: int = 3,
) -> tuple[list[CollectedDocument], list[str]]:
    resolved_documents: list[CollectedDocument] = []
    warnings: list[str] = []
    for document in documents:
        current = document
        visited: set[str] = set()
        for _ in range(max_hops):
            if current.source_url in visited:
                warnings.append(f"link resolution loop at {current.source_url}")
                break
            visited.add(current.source_url)
            resolver = next(
                (candidate for candidate in resolvers if candidate.matches(current.source_url)),
                None,
            )
            if resolver is None:
                break
            try:
                resolution = resolver.resolve(current.source_url, request)
            except Exception as error:
                warnings.append(f"{resolver.name} link {current.source_url}: {error}")
                break
            if resolution is None or resolution.canonical_url == current.source_url:
                break
            history = current.raw_metadata.get("link_resolutions")
            resolutions = list(history) if isinstance(history, list) else []
            resolutions.append(
                {
                    "resolver": resolution.resolver,
                    "source_url": resolution.source_url,
                    "canonical_url": resolution.canonical_url,
                    "raw_metadata": resolution.raw_metadata,
                }
            )
            discussion_urls = tuple(
                dict.fromkeys((*current.discussion_urls, resolution.discussion_url))
            )
            current = replace(
                current,
                source_url=resolution.canonical_url,
                title=resolution.title or current.title,
                raw_metadata=current.raw_metadata | {"link_resolutions": resolutions},
                discussion_urls=discussion_urls,
            )
        resolved_documents.append(current)
    return resolved_documents, warnings


def _hacker_news_item_id(url: str) -> int | None:
    raw_id = parse_qs(urlsplit(url).query).get("id", [None])[0]
    if not isinstance(raw_id, str) or not raw_id.isdigit():
        return None
    return int(raw_id)


def _is_http_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme.lower() in {"http", "https"} and bool(parts.hostname)
