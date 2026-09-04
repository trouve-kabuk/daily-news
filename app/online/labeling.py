from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from rich.console import Console, Group, RenderableType
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from app.config import TopicConfig
from app.knowledge.articles import create_or_resume_session
from app.knowledge.bookmarks import add_bookmark, toggle_bookmark
from app.knowledge.feedback import effective_feedback, feedback_history, record_feedback
from app.knowledge.translations import translated_field
from app.llm.runtime import InferenceError, TextInference
from app.online.summary import ArticlePageFetcher, SummaryUnavailable, get_or_create_summary

Input = Callable[[str], str]
Output = Callable[[RenderableType], None]
OpenUrl = Callable[[str], bool]
_CONSOLE = Console()
EXCERPT_MAX_CHARS = 500
_EXCERPT_MORE_HINT = "… [s]ummary for more"
REASON_SHORTCUTS = {
    "o": "off-topic",
    "w": "weak",
    "d": "duplicate",
    "s": "stale",
    "i": "inaccessible",
    "r": "other",
}
STANDARD_REASONS = frozenset(REASON_SHORTCUTS.values())


@dataclass(frozen=True)
class ReviewResult:
    decided: int
    deferred: int
    remaining: int
    session_id: str


def _excerpt_for_display(value: object | None) -> str | None:
    if value is None:
        return None
    excerpt = str(value).strip()
    if len(excerpt) <= EXCERPT_MAX_CHARS:
        return excerpt
    available = EXCERPT_MAX_CHARS - len(_EXCERPT_MORE_HINT)
    clipped = excerpt[:available].rstrip()
    word_boundary = clipped.rfind(" ")
    if word_boundary >= available // 2:
        clipped = clipped[:word_boundary].rstrip()
    return f"{clipped}{_EXCERPT_MORE_HINT}"


def _render_article(
    row: sqlite3.Row,
    index: int,
    total: int,
    title_translation: str | None,
    excerpt_translation: str | None,
    translation_target: str | None,
    source_name: str,
) -> Panel:
    markets = row["markets"] or "unknown"
    excerpt = _excerpt_for_display(row["excerpt"])
    translated_excerpt = _excerpt_for_display(excerpt_translation)
    metadata = Table.grid(padding=(0, 1))
    metadata.add_column(width=11, style="bold cyan")
    metadata.add_column(ratio=1)
    metadata.add_row("SOURCE", Text(f"{source_name}  [{row['source_id']}]", style="bold"))
    metadata.add_row("LANGUAGE", Text(str(row["language"]).upper(), style="bold magenta"))
    metadata.add_row("MARKETS", Text(str(markets), style="bold yellow"))
    metadata.add_row("PUBLISHED", str(row["published_at"] or "unknown"))

    contents: list[RenderableType] = [Text(str(row["title"]), style="bold bright_white"), metadata]
    if excerpt:
        contents.extend([Rule("Excerpt", style="dim"), Text(excerpt)])
    if translation_target:
        contents.extend(
            [
                Rule(f"{translation_target.upper()} translation", style="dim"),
                Text(str(title_translation), style="bold"),
            ]
        )
        if translated_excerpt:
            contents.append(Text(translated_excerpt))
    url = str(row["canonical_url"])
    link = Text("LINK       ", style="bold blue")
    link.append(url, style=f"blue underline link {url}")
    contents.extend([Rule(style="dim"), link])
    discussion_urls = str(row["discussion_urls"] or "").splitlines()
    for discussion_url in discussion_urls:
        discussion_link = Text("DISCUSS    ", style="bold yellow")
        discussion_link.append(
            discussion_url,
            style=f"yellow underline link {discussion_url}",
        )
        contents.append(discussion_link)
    return Panel(
        Group(*contents),
        title=f"[bold]Review {index}/{total}[/bold]  •  Article #{row['id']}",
        border_style="cyan",
        padding=(1, 2),
    )


def _console_output(value: RenderableType) -> None:
    _CONSOLE.print(value)


def review_topic(
    connection: sqlite3.Connection,
    topic: TopicConfig,
    *,
    lane_id: str | None = None,
    inference: TextInference | None = None,
    input_fn: Input = input,
    output_fn: Output = _console_output,
    open_url: OpenUrl | None = None,
    source_names: dict[str, str] | None = None,
    page_fetcher: ArticlePageFetcher | None = None,
    retain_content_by_source: dict[str, bool] | None = None,
    preferred_languages: tuple[str, ...] = ("en",),
) -> ReviewResult:
    if not preferred_languages:
        raise ValueError("preferred_languages must contain at least one language")
    translation_target = preferred_languages[0]
    session_id, queue, position = create_or_resume_session(connection, topic, lane_id)
    decided = deferred = 0
    while position < len(queue):
        candidate = queue[position]
        row = connection.execute(
            """SELECT a.*, GROUP_CONCAT(am.market) AS markets,
                      (SELECT GROUP_CONCAT(url, char(10)) FROM article_links
                       WHERE article_id=a.id AND kind='discussion') AS discussion_urls
               FROM articles a LEFT JOIN article_markets am ON am.article_id=a.id
               WHERE a.id=? GROUP BY a.id""",
            (candidate.article_id,),
        ).fetchone()
        if not row:
            position = _advance(connection, session_id, position)
            continue
        if effective_feedback(connection, topic.id, candidate.article_id) is not None:
            position = _advance(connection, session_id, position)
            continue
        title_translation = excerpt_translation = None
        needs_translation = row["language"] not in preferred_languages
        if needs_translation:
            try:
                title_translation = translated_field(
                    connection,
                    article_id=candidate.article_id,
                    field="title",
                    text=row["title"],
                    source_language=row["language"],
                    target_language=translation_target,
                    inference=inference,
                    progress=lambda message: output_fn(Text(f"  › {message}", style="dim cyan")),
                )
                excerpt_translation = translated_field(
                    connection,
                    article_id=candidate.article_id,
                    field="excerpt",
                    text=row["excerpt"],
                    source_language=row["language"],
                    target_language=translation_target,
                    inference=inference,
                    progress=lambda message: output_fn(Text(f"  › {message}", style="dim cyan")),
                )
            except InferenceError as error:
                output_fn(f"Translation deferred for article {candidate.article_id}: {error}")
            if not title_translation or (row["excerpt"] and not excerpt_translation):
                deferred += 1
                position = _advance(connection, session_id, position)
                continue
        output_fn(
            _render_article(
                row,
                position + 1,
                len(queue),
                title_translation,
                excerpt_translation,
                translation_target if needs_translation else None,
                (source_names or {}).get(str(row["source_id"]), str(row["source_id"])),
            )
        )
        command = (
            input_fn(
                "Decision [y]es/[n]o/[m]aybe/[b]ookmark/[yb]yes+bookmark/"
                "[o]pen/[s]ummary/[t]ext/[u]ndo/[q]uit: "
            )
            .strip()
            .lower()
        )
        if command in {"q", "quit"}:
            break
        if command in {"o", "open"}:
            if open_url:
                open_url(str(row["canonical_url"]))
            continue
        if command in {"b", "bookmark"}:
            saved = toggle_bookmark(
                connection,
                topic_id=topic.id,
                article_id=candidate.article_id,
            )
            message = "Bookmarked for later." if saved else "Bookmark removed."
            output_fn(Text(f"  ★ {message}", style="bold yellow"))
            continue
        if command in {"s", "summary"}:
            try:
                summary = get_or_create_summary(
                    connection,
                    article_id=candidate.article_id,
                    url=str(row["canonical_url"]),
                    inference=inference,
                    fetcher=page_fetcher,
                    retain_content=(retain_content_by_source or {}).get(
                        str(row["source_id"]), True
                    ),
                    progress=lambda message: output_fn(Text(f"  › {message}", style="dim cyan")),
                )
                cache_label = "cached" if summary.cached else "generated locally"
                output_fn(
                    Panel(
                        Text(summary.text),
                        title=f"[bold green]Short summary[/bold green]  •  {cache_label}",
                        border_style="green",
                        padding=(1, 2),
                    )
                )
            except (SummaryUnavailable, InferenceError) as error:
                output_fn(
                    Panel(
                        Text(str(error)),
                        title="[bold red]Summary unavailable[/bold red]",
                        border_style="red",
                    )
                )
            continue
        if command in {"t", "text"}:
            current_content = connection.execute(
                "SELECT content FROM articles WHERE id=?", (candidate.article_id,)
            ).fetchone()["content"]
            translated = current_content
            if needs_translation:
                translated = translated_field(
                    connection,
                    article_id=candidate.article_id,
                    field="content",
                    text=current_content,
                    source_language=row["language"],
                    target_language=translation_target,
                    inference=inference,
                    progress=lambda message: output_fn(Text(f"  › {message}", style="dim cyan")),
                )
            output_fn(translated or current_content or "No extracted text is available.")
            continue
        if command in {"u", "undo"}:
            previous = _latest_session_decision(connection, session_id)
            if previous is None:
                output_fn("Nothing to undo in this session.")
                continue
            record_feedback(
                connection,
                topic_id=topic.id,
                topic_version=topic.version,
                article_id=int(previous["article_id"]),
                action="undo",
                session_id=session_id,
            )
            position = next(
                index
                for index, queued in enumerate(queue)
                if queued.article_id == int(previous["article_id"])
            )
            _set_position(connection, session_id, position)
            output_fn("Latest decision undone.")
            continue
        bookmark_with_yes = command in {"yb", "yes+bookmark"}
        if bookmark_with_yes:
            add_bookmark(
                connection,
                topic_id=topic.id,
                article_id=candidate.article_id,
            )
            command = "yes"
        actions = {"y": "yes", "yes": "yes", "n": "no", "no": "no", "m": "maybe", "maybe": "maybe"}
        action = actions.get(command)
        if action is None:
            output_fn("Unknown command.")
            continue
        reason = None
        if action in {"no", "maybe"}:
            reason_input = input_fn(
                "Optional reason: [o]ff-topic / [w]eak / [d]uplicate / [s]tale / "
                "[i]naccessible / othe[r] / Enter to skip: "
            ).strip()
            normalized_reason = reason_input.lower()
            reason = (
                REASON_SHORTCUTS.get(
                    normalized_reason,
                    normalized_reason if normalized_reason in STANDARD_REASONS else reason_input,
                )
                or None
            )
        record_feedback(
            connection,
            topic_id=topic.id,
            topic_version=topic.version,
            article_id=candidate.article_id,
            action=action,  # type: ignore[arg-type]
            reason=reason,
            session_id=session_id,
        )
        if bookmark_with_yes:
            output_fn(Text("  ★ Bookmarked and marked yes.", style="bold yellow"))
        decided += 1
        position = _advance(connection, session_id, position)
    status = "completed" if position >= len(queue) else "active"
    connection.execute(
        "UPDATE review_sessions SET status=?, updated_at=? WHERE id=?",
        (status, datetime.now(UTC).isoformat(), session_id),
    )
    return ReviewResult(decided, deferred, len(queue) - position, session_id)


def _advance(connection: sqlite3.Connection, session_id: str, position: int) -> int:
    position += 1
    _set_position(connection, session_id, position)
    return position


def _set_position(connection: sqlite3.Connection, session_id: str, position: int) -> None:
    connection.execute(
        "UPDATE review_sessions SET position=?, updated_at=? WHERE id=?",
        (position, datetime.now(UTC).isoformat(), session_id),
    )


def _latest_session_decision(connection: sqlite3.Connection, session_id: str) -> sqlite3.Row | None:
    row: sqlite3.Row | None = connection.execute(
        """SELECT f.* FROM feedback_events f WHERE f.session_id=? AND f.action!='undo'
           AND NOT EXISTS (SELECT 1 FROM feedback_events u WHERE u.action='undo'
                           AND u.compensates_event_id=f.id)
           ORDER BY f.id DESC LIMIT 1""",
        (session_id,),
    ).fetchone()
    return row


def article_feedback(
    connection: sqlite3.Connection, topic_id: str, article_id: int
) -> tuple[object, list[dict[str, object]]]:
    return (
        effective_feedback(connection, topic_id, article_id),
        feedback_history(connection, topic_id, article_id),
    )
