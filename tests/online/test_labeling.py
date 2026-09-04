from __future__ import annotations

from datetime import UTC, datetime, timedelta
from io import StringIO

from rich.console import Console

from app.config import EditionConfig, Settings
from app.knowledge.articles import upsert_article
from app.knowledge.feedback import effective_feedback, record_feedback
from app.knowledge.store import Store
from app.knowledge.topics import persist_topic
from app.llm.runtime import InferenceError
from app.online.feed import create_edition
from app.online.labeling import EXCERPT_MAX_CHARS, _excerpt_for_display, review_topic
from app.online.summary import FetchedPage


def seed_articles(settings: Settings, store: Store, languages: list[str]) -> list[int]:
    topic = settings.topics["ai-engineering"]
    now = datetime.now(UTC)
    ids: list[int] = []
    with store.transaction() as connection:
        persist_topic(connection, topic)
        for index, language in enumerate(languages):
            article_id = upsert_article(
                connection,
                canonical_url=f"https://example.com/{language}/{index}",
                stable_id=None,
                source_id="openai-news",
                title=f"Article {index}",
                excerpt=f"Excerpt {index}",
                author=None,
                language=language,
                published_at=now - timedelta(minutes=index),
                updated_at=None,
                discovered_at=now,
                fetched_at=now,
                content=f"Content {index}",
            )
            connection.execute(
                """INSERT INTO article_markets
                   (article_id, market, confidence, evidence, classifier_version)
                   VALUES (?, 'global', 1, 'test', 'test-v1')""",
                (article_id,),
            )
            if language == "ja":
                connection.execute(
                    """INSERT INTO article_markets
                       (article_id, market, confidence, evidence, classifier_version)
                       VALUES (?, 'JP', 1, 'test', 'test-v1')""",
                    (article_id,),
                )
            connection.execute(
                """INSERT INTO topic_articles
                   (topic_id, topic_version, article_id, source_id, discovered_at)
                   VALUES (?, ?, ?, 'openai-news', ?)""",
                (topic.id, topic.version, article_id, now.isoformat()),
            )
            ids.append(article_id)
    return ids


def test_review_yes_no_maybe_undo_quit_and_resume(settings: Settings, store: Store) -> None:
    topic = settings.topics["ai-engineering"]
    article_ids = seed_articles(settings, store, ["en", "en", "en"])

    first_inputs = iter(["yes", "quit"])
    with store.transaction() as connection:
        first = review_topic(
            connection,
            topic,
            input_fn=lambda _: next(first_inputs),
            output_fn=lambda _: None,
        )
    assert first.decided == 1
    assert first.remaining == 2

    second_inputs = iter(["undo", "maybe", "", "yes", "quit"])
    with store.transaction() as connection:
        second = review_topic(
            connection,
            topic,
            input_fn=lambda _: next(second_inputs),
            output_fn=lambda _: None,
        )
        assert effective_feedback(connection, topic.id, article_ids[0]).action == "maybe"
        assert effective_feedback(connection, topic.id, article_ids[1]).action == "yes"
    assert second.remaining == 1

    third_inputs = iter(["no", "w"])
    with store.transaction() as connection:
        third = review_topic(
            connection,
            topic,
            input_fn=lambda _: next(third_inputs),
            output_fn=lambda _: None,
        )
        final = effective_feedback(connection, topic.id, article_ids[2])
    assert final is not None and (final.action, final.reason) == ("no", "weak")
    assert third.remaining == 0


def test_feedback_reason_prompt_shows_shortcuts(settings: Settings, store: Store) -> None:
    topic = settings.topics["ai-engineering"]
    seed_articles(settings, store, ["en"])
    inputs = iter(["n", "", "q"])
    prompts: list[str] = []

    def input_with_prompt(prompt: str) -> str:
        prompts.append(prompt)
        return next(inputs)

    with store.transaction() as connection:
        review_topic(connection, topic, input_fn=input_with_prompt, output_fn=lambda _: None)

    assert "[w]eak" in prompts[1]
    assert "[d]uplicate" in prompts[1]
    assert "othe[r]" in prompts[1]


def test_bookmark_shortcut_saves_without_feedback_or_advancing(
    settings: Settings, store: Store
) -> None:
    topic = settings.topics["ai-engineering"]
    article_id = seed_articles(settings, store, ["en"])[0]
    inputs = iter(["b", "q"])
    output = StringIO()
    console = Console(file=output, width=90, color_system=None)

    with store.transaction() as connection:
        result = review_topic(
            connection,
            topic,
            input_fn=lambda _: next(inputs),
            output_fn=console.print,
        )
        bookmark = connection.execute("SELECT topic_id, article_id FROM bookmarks").fetchone()
        assert effective_feedback(connection, topic.id, article_id) is None

    assert tuple(bookmark) == (topic.id, article_id)
    assert "Bookmarked for later" in output.getvalue()
    assert result.decided == 0 and result.remaining == 1


def test_yes_bookmark_shortcut_saves_records_yes_and_advances(
    settings: Settings, store: Store
) -> None:
    topic = settings.topics["ai-engineering"]
    article_id = seed_articles(settings, store, ["en"])[0]
    prompts: list[str] = []

    def choose_yes_bookmark(prompt: str) -> str:
        prompts.append(prompt)
        return "yb"

    with store.transaction() as connection:
        result = review_topic(
            connection,
            topic,
            input_fn=choose_yes_bookmark,
            output_fn=lambda _: None,
        )
        feedback = effective_feedback(connection, topic.id, article_id)
        bookmarked = connection.execute(
            "SELECT 1 FROM bookmarks WHERE topic_id=? AND article_id=?",
            (topic.id, article_id),
        ).fetchone()

    assert "[yb]yes+bookmark" in prompts[0]
    assert feedback is not None and feedback.action == "yes"
    assert bookmarked is not None
    assert result.decided == 1 and result.remaining == 0


class FakeInference:
    model_version = "fake-v1"

    def prepare(self) -> None:
        pass

    def translate(self, text: str, source_language: str, target_language: str = "en") -> str:
        return f"English: {text}"

    def summarize(self, text: str, target_language: str = "en") -> str:
        return "A concise local summary of the extracted article."


class RecordingInference(FakeInference):
    def __init__(self) -> None:
        self.translation_targets: list[str] = []

    def translate(self, text: str, source_language: str, target_language: str = "en") -> str:
        self.translation_targets.append(target_language)
        return f"{target_language.upper()}: {text}"


class FakePageFetcher:
    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, url: str) -> FetchedPage:
        self.calls += 1
        paragraphs = "".join(
            f"<p>Paragraph {index} explains a concrete technical result, its evidence, "
            "and why the release matters to software engineering teams.</p>"
            for index in range(8)
        )
        return FetchedPage(
            requested_url=url,
            final_url=url,
            status_code=200,
            headers={"content-type": "text/html"},
            html=f"<html><body><article><h1>Article</h1>{paragraphs}</article></body></html>",
        )


class BrokenSummaryInference(FakeInference):
    model_version = "missing/model"

    def summarize(self, text: str, target_language: str = "en") -> str:
        raise InferenceError("Unable to load MLX model 'missing/model': repository not found")


def test_non_english_review_is_deferred_until_translation_exists(
    settings: Settings, store: Store
) -> None:
    topic = settings.topics["ai-engineering"]
    article_id = seed_articles(settings, store, ["ja"])[0]
    with store.transaction() as connection:
        deferred = review_topic(
            connection, topic, input_fn=lambda _: "yes", output_fn=lambda _: None
        )
        assert effective_feedback(connection, topic.id, article_id) is None
        assert connection.execute("SELECT COUNT(*) FROM processing_outcomes").fetchone()[0] == 2
    assert deferred.deferred == 1

    progress: list[str] = []
    with store.transaction() as connection:
        reviewed = review_topic(
            connection,
            topic,
            inference=FakeInference(),
            input_fn=lambda _: "yes",
            output_fn=lambda value: progress.append(str(value)),
        )
        assert effective_feedback(connection, topic.id, article_id).action == "yes"
        assert connection.execute("SELECT COUNT(*) FROM translations").fetchone()[0] == 2
    assert reviewed.decided == 1
    assert any("Translating title from JA to EN with fake-v1" in item for item in progress)
    assert any("Translating excerpt from JA to EN with fake-v1" in item for item in progress)


def test_preferred_language_is_native_and_first_language_is_translation_target(
    settings: Settings, store: Store
) -> None:
    topic = settings.topics["ai-engineering"]
    japanese_id = seed_articles(settings, store, ["ja"])[0]
    with store.transaction() as connection:
        native = review_topic(
            connection,
            topic,
            preferred_languages=("en", "ja"),
            input_fn=lambda _: "yes",
            output_fn=lambda _: None,
        )
        assert effective_feedback(connection, topic.id, japanese_id).action == "yes"
        assert connection.execute("SELECT COUNT(*) FROM translations").fetchone()[0] == 0
    assert native.deferred == 0

    english_id = seed_articles(settings, store, ["en"])[0]
    inference = RecordingInference()
    output = StringIO()
    console = Console(file=output, width=90, color_system=None)
    with store.transaction() as connection:
        translated = review_topic(
            connection,
            topic,
            preferred_languages=("ja",),
            inference=inference,
            input_fn=lambda _: "yes",
            output_fn=console.print,
        )
        targets = connection.execute(
            "SELECT DISTINCT target_language FROM translations WHERE article_id=?",
            (english_id,),
        ).fetchall()
    assert translated.deferred == 0
    assert inference.translation_targets == ["ja", "ja"]
    assert [row[0] for row in targets] == ["ja"]
    assert "Translating title from EN to JA" in output.getvalue()
    assert "JA translation" in output.getvalue()


def test_edition_honors_topic_and_lane_hard_maximums(settings: Settings, store: Store) -> None:
    topic = settings.topics["ai-engineering"]
    article_ids = seed_articles(settings, store, ["en", "en", "en"])
    limited_lanes = [
        lane.model_copy(update={"target_articles": 1, "max_articles": 1})
        if lane.id == "global-english"
        else lane
        for lane in topic.coverage
    ]
    limited_topic = topic.model_copy(
        update={"edition": EditionConfig(max_articles=2), "coverage": limited_lanes}
    )
    with store.transaction() as connection:
        for article_id in article_ids:
            record_feedback(
                connection,
                topic_id=topic.id,
                topic_version=topic.version,
                article_id=article_id,
                action="yes",
            )
        edition = create_edition(connection, limited_topic)
    assert len(edition) == 1
    assert edition[0]["primary_lane_id"] == "global-english"


def test_review_renders_framed_emphasized_metadata(settings: Settings, store: Store) -> None:
    topic = settings.topics["ai-engineering"]
    article_id = seed_articles(settings, store, ["en"])[0]
    output = StringIO()
    console = Console(file=output, width=90, color_system=None)
    with store.transaction() as connection:
        connection.execute(
            """INSERT INTO article_links
               (article_id, kind, source_id, url, discovered_at)
               VALUES (?, 'discussion', 'hacker-news', ?, ?)""",
            (
                article_id,
                "https://news.ycombinator.com/item?id=101",
                datetime.now(UTC).isoformat(),
            ),
        )
        review_topic(
            connection,
            topic,
            input_fn=lambda _: "quit",
            output_fn=console.print,
            source_names={"openai-news": "OpenAI News"},
        )
    rendered = output.getvalue()
    assert "╭" in rendered and "╰" in rendered
    assert "Article 0" in rendered
    assert "SOURCE" in rendered and "OpenAI News" in rendered
    assert "LANGUAGE" in rendered and "EN" in rendered
    assert "DISCUSS" in rendered and "news.ycombinator.com/item?id=101" in rendered


def test_long_excerpt_is_capped_and_points_to_summary() -> None:
    excerpt = "A useful detail " * 80 + "hidden ending"
    displayed = _excerpt_for_display(excerpt)
    assert displayed is not None
    assert len(displayed) <= EXCERPT_MAX_CHARS
    assert displayed.endswith("… [s]ummary for more")
    assert "hidden ending" not in displayed


def test_summary_shortcut_fetches_extracts_and_caches_without_feedback(
    settings: Settings, store: Store
) -> None:
    topic = settings.topics["ai-engineering"]
    article_id = seed_articles(settings, store, ["en"])[0]
    fetcher = FakePageFetcher()
    commands = iter(["summary", "s", "quit"])
    output = StringIO()
    console = Console(file=output, width=90, color_system=None)
    with store.transaction() as connection:
        result = review_topic(
            connection,
            topic,
            inference=FakeInference(),
            input_fn=lambda _: next(commands),
            output_fn=console.print,
            page_fetcher=fetcher,
        )
        assert effective_feedback(connection, topic.id, article_id) is None
        assert (
            connection.execute("SELECT COUNT(*) FROM article_content_versions").fetchone()[0] == 1
        )
        assert connection.execute("SELECT COUNT(*) FROM article_summaries").fetchone()[0] == 1
        assert connection.execute("SELECT LENGTH(content) FROM articles").fetchone()[0] > 200
    rendered = output.getvalue()
    assert "Short summary" in rendered
    assert "generated locally" in rendered and "cached" in rendered
    assert "fetching article HTML" in rendered
    assert "Loading AI model" in rendered
    assert "Generating summary" in rendered
    assert fetcher.calls == 1
    assert result.decided == 0 and result.remaining == 1


def test_summary_model_failure_is_shown_without_ending_review(
    settings: Settings, store: Store
) -> None:
    topic = settings.topics["ai-engineering"]
    article_id = seed_articles(settings, store, ["en"])[0]
    commands = iter(["s", "quit"])
    output = StringIO()
    console = Console(file=output, width=90, color_system=None)
    with store.transaction() as connection:
        result = review_topic(
            connection,
            topic,
            inference=BrokenSummaryInference(),
            input_fn=lambda _: next(commands),
            output_fn=console.print,
            page_fetcher=FakePageFetcher(),
        )
        assert effective_feedback(connection, topic.id, article_id) is None
        outcome = connection.execute(
            "SELECT status, reason FROM processing_outcomes WHERE stage='summary'"
        ).fetchone()
    assert "Summary unavailable" in output.getvalue()
    assert "repository not found" in output.getvalue()
    assert result.remaining == 1
    assert tuple(outcome) == (
        "failed",
        "Unable to load MLX model 'missing/model': repository not found",
    )
