from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import yaml
from typer.testing import CliRunner

from app.cli import app
from app.config import Settings, SourceConfig
from app.knowledge.bookmarks import toggle_bookmark
from app.knowledge.feedback import effective_feedback
from app.knowledge.store import Store
from app.offline.collection import HttpResponse
from app.offline.pipeline import collect_topics
from app.online.feed import create_edition


class FixtureFetcher:
    def __init__(self, responses: dict[str, HttpResponse]) -> None:
        self.responses = responses

    def get(self, url: str, headers: dict[str, str]) -> HttpResponse:
        return self.responses[url]


def test_both_topics_collect_persist_review_and_publish(
    settings: Settings, store: Store, tmp_path: Path
) -> None:
    engineering = settings.topics["ai-engineering"].model_copy(update={"sources": ["openai-news"]})
    papers = settings.topics["ai-papers"].model_copy(update={"sources": ["arxiv-cs-ai"]})
    for source_id in ["openai-news", "arxiv-cs-ai"]:
        source = settings.sources[source_id]
        settings.sources[source_id] = SourceConfig.model_validate(
            source.model_dump() | {"retry": {"attempts": 1}, "rate_limit_seconds": 0}
        )
    fetcher = FixtureFetcher(
        {
            settings.sources["openai-news"].url: HttpResponse(
                200, Path("tests/fixtures/engineering.xml").read_bytes(), {}
            ),
            settings.sources["arxiv-cs-ai"].url: HttpResponse(
                200, Path("tests/fixtures/papers.xml").read_bytes(), {}
            ),
        }
    )
    now = datetime(2026, 9, 3, 2, tzinfo=UTC)
    with store.transaction() as connection:
        report = collect_topics(
            connection, settings, [engineering, papers], fetcher, now=now, sleep=lambda _: None
        )
        assert report.sources_succeeded == 2
    config_dir = tmp_path / "config"
    (config_dir / "topics").mkdir(parents=True)
    (config_dir / "sources.yaml").write_text(
        yaml.safe_dump(
            {
                "sources": [
                    settings.sources["openai-news"].model_dump(mode="json"),
                    settings.sources["arxiv-cs-ai"].model_dump(mode="json"),
                ]
            }
        )
    )
    for topic in [engineering, papers]:
        (config_dir / "topics" / f"{topic.id}.yaml").write_text(
            yaml.safe_dump(topic.model_dump(mode="json"))
        )

    runner = CliRunner()
    reviewed_articles: dict[str, int] = {}
    for topic in [engineering, papers]:
        result = runner.invoke(
            app,
            [
                "review",
                topic.id,
                "--config",
                str(config_dir),
                "--database",
                str(settings.database_path),
            ],
            input="yes\n",
        )
        assert result.exit_code == 0, result.output
        assert "1 decided" in result.output
        with store.transaction() as connection:
            article_id = connection.execute(
                "SELECT article_id FROM topic_articles WHERE topic_id=?", (topic.id,)
            ).fetchone()[0]
            reviewed_articles[topic.id] = article_id
            assert effective_feedback(connection, topic.id, article_id).action == "yes"
            edition = create_edition(connection, topic)
            assert len(edition) == 1

    bookmarked_article = reviewed_articles[engineering.id]
    with store.transaction() as connection:
        toggle_bookmark(
            connection,
            topic_id=engineering.id,
            article_id=bookmarked_article,
        )
    listed = runner.invoke(
        app,
        [
            "bookmarks",
            engineering.id,
            "--config",
            str(config_dir),
            "--database",
            str(settings.database_path),
        ],
    )
    assert listed.exit_code == 0, listed.output
    assert "Bookmarks" in listed.output and str(bookmarked_article) in listed.output

    removed = runner.invoke(
        app,
        [
            "unbookmark",
            engineering.id,
            str(bookmarked_article),
            "--config",
            str(config_dir),
            "--database",
            str(settings.database_path),
        ],
    )
    assert removed.exit_code == 0, removed.output
    assert "Bookmark removed" in removed.output
