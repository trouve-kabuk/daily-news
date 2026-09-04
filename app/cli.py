from __future__ import annotations

import json
import webbrowser
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from app.config import ConfigurationError, LLMSettings, Settings, default_config_dir, load_settings
from app.knowledge.bookmarks import list_bookmarks, remove_bookmark
from app.knowledge.store import Store
from app.knowledge.topics import persist_topic
from app.llm.llama_cpp import LlamaCppInference
from app.llm.mlx import MLXInference
from app.llm.runtime import TextInference
from app.offline.collection import HttpxFetcher
from app.offline.pipeline import collect_topics
from app.online.feed import create_edition, hide_entry
from app.online.labeling import article_feedback, review_topic
from app.online.summary import HttpxArticlePageFetcher

app = typer.Typer(no_args_is_help=True, help="Collect and review a local daily news stream.")
DEFAULT_CONFIG_DIR = default_config_dir()
_CONSOLE = Console()


def _local_inference(config: LLMSettings) -> TextInference | None:
    if config.model is None:
        return None
    if config.provider == "llama_cpp":
        return LlamaCppInference(config.model)
    if not isinstance(config.model, str):
        raise AssertionError("validated MLX configuration must contain a model identifier")
    return MLXInference(config.model)


def _settings(config: Path, database: Path) -> Settings:
    try:
        settings = load_settings(config, database)
    except ConfigurationError as error:
        raise typer.BadParameter(str(error)) from error
    store = Store(settings.database_path)
    store.initialize()
    with store.transaction() as connection:
        for topic in settings.topics.values():
            try:
                persist_topic(connection, topic)
            except ValueError as error:
                raise typer.BadParameter(str(error)) from error
    return settings


ConfigOption = Annotated[
    Path,
    typer.Option(
        "--config",
        help="Configuration directory",
        exists=True,
        file_okay=False,
        envvar="DAILY_NEWS_CONFIG",
    ),
]
DatabaseOption = Annotated[
    Path,
    typer.Option("--database", help="SQLite database path", envvar="DAILY_NEWS_DATABASE"),
]


@app.command("topics")
def list_topics(
    config: ConfigOption = DEFAULT_CONFIG_DIR,
    database: DatabaseOption = Path("daily-news.sqlite3"),
) -> None:
    """List topics and their source and coverage configuration."""
    settings = _settings(config, database)
    for topic in settings.topics.values():
        typer.echo(f"{topic.id} (v{topic.version}): {topic.name}")
        sources = [
            source_id if settings.sources[source_id].enabled else f"{source_id} (disabled)"
            for source_id in topic.sources
        ]
        typer.echo(f"  sources: {', '.join(sources)}")
        for lane in topic.coverage:
            typer.echo(
                f"  {lane.id}: languages={','.join(lane.languages)} "
                f"markets={','.join(lane.markets)} target={lane.target_articles or '-'} "
                f"max={lane.max_articles or '-'}"
            )


@app.command()
def collect(
    topic_id: Annotated[str | None, typer.Option("--topic", help="One topic ID")] = None,
    config: ConfigOption = DEFAULT_CONFIG_DIR,
    database: DatabaseOption = Path("daily-news.sqlite3"),
) -> None:
    """Collect recent articles for one topic or every configured topic."""
    settings = _settings(config, database)
    if topic_id and topic_id not in settings.topics:
        raise typer.BadParameter(f"unknown topic: {topic_id}")
    topics = [settings.topics[topic_id]] if topic_id else list(settings.topics.values())
    with HttpxFetcher() as fetcher, Store(database).transaction() as connection:
        report = collect_topics(
            connection,
            settings,
            topics,
            fetcher,
            on_source_start=lambda source_name: typer.echo(f"Collecting {source_name}..."),
            on_retry=lambda source_name, error, delay, attempt, total: typer.echo(
                f"  {source_name}: {error}; retrying in {delay:g}s (attempt {attempt}/{total})..."
            ),
        )
    typer.echo(
        f"Run {report.run_id}: {report.articles_prepared} prepared, "
        f"{report.preparation_failures} preparation failures, "
        f"{report.sources_succeeded} sources succeeded."
    )
    for failure in report.failures:
        typer.echo(f"Source {failure.source_id} reported an issue: {failure.error}", err=True)


@app.command()
def review(
    topic_id: Annotated[str, typer.Argument(help="Topic ID")],
    lane: Annotated[str | None, typer.Option("--lane", help="Coverage lane ID")] = None,
    config: ConfigOption = DEFAULT_CONFIG_DIR,
    database: DatabaseOption = Path("daily-news.sqlite3"),
) -> None:
    """Start or resume interactive candidate review."""
    settings = _settings(config, database)
    topic = settings.topics.get(topic_id)
    if topic is None:
        raise typer.BadParameter(f"unknown topic: {topic_id}")
    inference = _local_inference(settings.llm)
    with HttpxArticlePageFetcher() as page_fetcher, Store(database).transaction() as connection:
        try:
            result = review_topic(
                connection,
                topic,
                lane_id=lane,
                inference=inference,
                open_url=webbrowser.open,
                source_names={source.id: source.name for source in settings.sources.values()},
                page_fetcher=page_fetcher,
                retain_content_by_source={
                    source.id: source.retain_content for source in settings.sources.values()
                },
                preferred_languages=tuple(settings.preferred_languages),
            )
        except ValueError as error:
            raise typer.BadParameter(str(error)) from error
    typer.echo(
        f"Session {result.session_id}: {result.decided} decided, "
        f"{result.deferred} translation-deferred, {result.remaining} remaining."
    )


@app.command("feedback")
def show_feedback(
    topic_id: Annotated[str, typer.Argument(help="Topic ID")],
    article_id: Annotated[int, typer.Argument(help="Article database ID")],
    config: ConfigOption = DEFAULT_CONFIG_DIR,
    database: DatabaseOption = Path("daily-news.sqlite3"),
) -> None:
    """Show effective feedback and immutable event history."""
    _settings(config, database)
    with Store(database).transaction() as connection:
        effective, history = article_feedback(connection, topic_id, article_id)
    typer.echo(f"Effective: {effective or 'none'}")
    typer.echo(json.dumps(history, indent=2, ensure_ascii=False))


@app.command("bookmarks")
def show_bookmarks(
    topic_id: Annotated[str | None, typer.Argument(help="Optional topic ID")] = None,
    config: ConfigOption = DEFAULT_CONFIG_DIR,
    database: DatabaseOption = Path("daily-news.sqlite3"),
) -> None:
    """List articles bookmarked during review."""
    settings = _settings(config, database)
    if topic_id and topic_id not in settings.topics:
        raise typer.BadParameter(f"unknown topic: {topic_id}")
    with Store(database).transaction() as connection:
        saved = list_bookmarks(connection, topic_id)
    if not saved:
        typer.echo("No bookmarks saved.")
        return
    table = Table(title="Bookmarks", header_style="bold cyan")
    table.add_column("ARTICLE", justify="right", style="bold")
    table.add_column("TOPIC", style="cyan")
    table.add_column("SOURCE", style="magenta")
    table.add_column("TITLE", ratio=1)
    table.add_column("SAVED", style="dim")
    for bookmark in saved:
        title_and_link = Text(bookmark.title)
        title_and_link.append("\n")
        title_and_link.append(
            bookmark.canonical_url,
            style=f"blue underline link {bookmark.canonical_url}",
        )
        for discussion_url in (bookmark.discussion_urls or "").splitlines():
            title_and_link.append("\nDiscussion: ", style="bold yellow")
            title_and_link.append(
                discussion_url,
                style=f"yellow underline link {discussion_url}",
            )
        table.add_row(
            str(bookmark.article_id),
            bookmark.topic_id,
            bookmark.source_id,
            title_and_link,
            bookmark.created_at,
        )
    _CONSOLE.print(table)


@app.command("unbookmark")
def unbookmark_article(
    topic_id: Annotated[str, typer.Argument(help="Topic ID")],
    article_id: Annotated[int, typer.Argument(help="Article database ID")],
    config: ConfigOption = DEFAULT_CONFIG_DIR,
    database: DatabaseOption = Path("daily-news.sqlite3"),
) -> None:
    """Remove a saved article bookmark."""
    settings = _settings(config, database)
    if topic_id not in settings.topics:
        raise typer.BadParameter(f"unknown topic: {topic_id}")
    with Store(database).transaction() as connection:
        removed = remove_bookmark(connection, topic_id=topic_id, article_id=article_id)
    typer.echo("Bookmark removed." if removed else "Bookmark was not saved.")


@app.command()
def feed(
    topic_id: Annotated[str, typer.Argument(help="Topic ID")],
    config: ConfigOption = DEFAULT_CONFIG_DIR,
    database: DatabaseOption = Path("daily-news.sqlite3"),
) -> None:
    """Create and display today's conservative, approved-only edition."""
    settings = _settings(config, database)
    topic = settings.topics.get(topic_id)
    if topic is None:
        raise typer.BadParameter(f"unknown topic: {topic_id}")
    with Store(database).transaction() as connection:
        entries = create_edition(connection, topic)
    if not entries:
        typer.echo("No approved eligible articles are available.")
    for entry in entries:
        rendered = (
            f"{entry['position']}. Article #{entry['article_id']} "
            f"[{entry['primary_lane_id']}] {entry['title']}\n"
            f"   {entry['canonical_url']}"
        )
        for discussion_url in str(entry["discussion_urls"] or "").splitlines():
            rendered += f"\n   Discussion: {discussion_url}"
        typer.echo(rendered)


@app.command()
def hide(
    topic_id: Annotated[str, typer.Argument(help="Topic ID")],
    article_id: Annotated[int, typer.Argument(help="Article database ID")],
    reason: Annotated[str | None, typer.Option("--reason")] = None,
    config: ConfigOption = DEFAULT_CONFIG_DIR,
    database: DatabaseOption = Path("daily-news.sqlite3"),
) -> None:
    """Hide a feed article and make the next approved candidate eligible."""
    settings = _settings(config, database)
    topic = settings.topics.get(topic_id)
    if topic is None:
        raise typer.BadParameter(f"unknown topic: {topic_id}")
    with Store(database).transaction() as connection:
        article = connection.execute("SELECT 1 FROM articles WHERE id=?", (article_id,)).fetchone()
        if article is None:
            raise typer.BadParameter(f"unknown article: {article_id}")
        hide_entry(connection, topic, article_id, reason)
        replacement = create_edition(connection, topic)
    typer.echo(f"Article {article_id} hidden. Edition now contains {len(replacement)} articles.")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
