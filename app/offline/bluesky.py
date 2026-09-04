from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast
from urllib.parse import quote, urlencode, urlsplit

from app.config import SourceConfig

if TYPE_CHECKING:
    from app.offline.collection import CollectedDocument, HttpResponse


@dataclass(frozen=True)
class BlueskyPage:
    documents: list[CollectedDocument]
    cursor: str | None
    posts_examined: int
    warnings: tuple[str, ...] = ()


def search_url(source: SourceConfig, *, cursor: str | None = None, limit: int) -> str:
    settings = source.bluesky
    if settings is None:
        raise ValueError("Bluesky search source is missing its search configuration")
    parameters: list[tuple[str, str]] = [
        ("q", settings.query),
        ("sort", settings.sort),
        ("limit", str(limit)),
    ]
    if settings.language:
        parameters.append(("lang", settings.language))
    if cursor:
        parameters.append(("cursor", cursor))
    return f"{source.url}?{urlencode(parameters)}"


def parse_search_page(
    content: bytes,
    source: SourceConfig,
    *,
    response_headers: dict[str, str] | None = None,
) -> BlueskyPage:
    try:
        payload = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"invalid Bluesky search JSON: {error}") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("posts"), list):
        raise ValueError("Bluesky search response must contain a posts array")

    documents: list[CollectedDocument] = []
    warnings: list[str] = []
    raw_posts = payload["posts"]
    for index, raw_post in enumerate(raw_posts):
        if not isinstance(raw_post, dict):
            warnings.append(f"post {index}: expected an object")
            continue
        try:
            document = _parse_post(cast(dict[str, object], raw_post), source)
        except ValueError as error:
            uri = raw_post.get("uri")
            warnings.append(f"post {uri or index}: {error}")
            continue
        if document is not None:
            documents.append(replace(document, response_metadata=response_headers))

    cursor = payload.get("cursor")
    return BlueskyPage(
        documents=documents,
        cursor=cursor if isinstance(cursor, str) and cursor else None,
        posts_examined=len(raw_posts),
        warnings=tuple(warnings),
    )


def collect_search(
    source: SourceConfig,
    first_response: HttpResponse,
    request: Callable[[SourceConfig, str, dict[str, str] | None], HttpResponse],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[CollectedDocument], list[str]]:
    documents: list[CollectedDocument] = []
    warnings: list[str] = []
    posts_examined = 0
    seen_posts: set[str] = set()
    seen_cursors: set[str] = set()
    response = first_response

    while posts_examined < source.max_items:
        page = parse_search_page(
            response.content,
            source,
            response_headers=response.headers,
        )
        posts_examined += page.posts_examined
        warnings.extend(page.warnings)
        for document in page.documents:
            post_identity = str(
                document.raw_metadata.get("uri")
                or (document.discussion_urls[0] if document.discussion_urls else "")
            )
            if post_identity not in seen_posts:
                seen_posts.add(post_identity)
                documents.append(document)

        if (
            page.cursor is None
            or page.cursor in seen_cursors
            or page.posts_examined == 0
            or posts_examined >= source.max_items
        ):
            break
        seen_cursors.add(page.cursor)
        if source.rate_limit_seconds:
            sleep(source.rate_limit_seconds)
        try:
            response = request(
                source,
                search_url(
                    source,
                    cursor=page.cursor,
                    limit=min(100, source.max_items - posts_examined),
                ),
                None,
            )
        except Exception as error:
            warnings.append(f"page after cursor {page.cursor!r}: {error}")
            break

    return documents, warnings


def _parse_post(raw_post: dict[str, object], source: SourceConfig) -> CollectedDocument | None:
    from app.offline.collection import CollectedDocument

    uri = raw_post.get("uri")
    author = raw_post.get("author")
    record = raw_post.get("record")
    if not isinstance(uri, str) or not uri.startswith("at://"):
        raise ValueError("missing valid AT URI")
    if not isinstance(author, dict) or not isinstance(record, dict):
        raise ValueError("missing author or record")

    text = record.get("text")
    created_at = record.get("createdAt")
    if not isinstance(text, str) or not isinstance(created_at, str):
        raise ValueError("missing post text or creation time")

    external = _external_link(raw_post, record)
    if external is None:
        return None
    canonical_url, external_title, external_description = external

    handle = author.get("handle")
    did = author.get("did")
    actor = handle if isinstance(handle, str) and handle else did
    if not isinstance(actor, str) or not actor:
        raise ValueError("missing author identity")
    post_key = uri.rsplit("/", 1)[-1]
    discussion_url = (
        f"https://bsky.app/profile/{quote(actor, safe=':.')}/post/{quote(post_key, safe='')}"
    )

    languages = record.get("langs")
    language = source.languages[0]
    if isinstance(languages, list):
        language = next((item for item in languages if isinstance(item, str) and item), language)

    title = external_title.strip() if external_title else ""
    if not title:
        title = _post_title(text)
    excerpt = text.strip() or external_description
    display_name = author.get("displayName")
    author_name = (
        str(display_name).strip()
        if isinstance(display_name, str) and display_name.strip()
        else f"@{actor}"
    )

    return CollectedDocument(
        source_url=canonical_url,
        title=title,
        excerpt=excerpt or None,
        author=author_name,
        language=language,
        published_at=_parse_datetime(created_at),
        updated_at=None,
        content=text.strip() or None,
        stable_id=None,
        raw_metadata=raw_post,
        discussion_urls=(discussion_url,),
    )


def _external_link(
    post: dict[str, object], record: dict[str, object]
) -> tuple[str, str | None, str | None] | None:
    embed = post.get("embed")
    candidates = [embed]
    if isinstance(embed, dict):
        candidates.append(embed.get("media"))
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        external = candidate.get("external")
        if not isinstance(external, dict):
            continue
        uri = external.get("uri")
        if isinstance(uri, str) and _is_external_http_url(uri):
            title = external.get("title")
            description = external.get("description")
            return (
                uri,
                title if isinstance(title, str) else None,
                description if isinstance(description, str) else None,
            )

    facets = record.get("facets")
    if isinstance(facets, list):
        for facet in facets:
            if not isinstance(facet, dict) or not isinstance(facet.get("features"), list):
                continue
            for feature in facet["features"]:
                if not isinstance(feature, dict):
                    continue
                uri = feature.get("uri")
                if (
                    feature.get("$type") == "app.bsky.richtext.facet#link"
                    and isinstance(uri, str)
                    and _is_external_http_url(uri)
                ):
                    return uri, None, None
    return None


def _is_external_http_url(value: str) -> bool:
    parts = urlsplit(value)
    hostname = (parts.hostname or "").lower()
    return parts.scheme.lower() in {"http", "https"} and bool(hostname) and hostname != "bsky.app"


def _post_title(text: str) -> str:
    normalized = " ".join(text.split())
    if not normalized:
        raise ValueError("post and external card have no usable title")
    return normalized if len(normalized) <= 180 else f"{normalized[:177].rstrip()}..."


def _parse_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("invalid post creation time") from error
    return parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
