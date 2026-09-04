from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from app.config import (
    ConfigurationError,
    HuggingFaceModel,
    LLMSettings,
    SourceConfig,
    UserSettings,
    load_settings,
)
from app.knowledge.store import Store
from app.knowledge.topics import persist_topic


def test_default_topics_and_coverage_are_valid() -> None:
    settings = load_settings(Path("config"), Path(":memory:"))
    assert set(settings.topics) == {"ai-engineering", "ai-papers"}
    assert settings.preferred_languages == ["en"]
    assert settings.llm.provider == "llama_cpp"
    assert settings.llm.model == HuggingFaceModel(
        repository="bartowski/Qwen_Qwen3.5-27B-GGUF",
        filename="Qwen3.5-27B-Q4_K_M.gguf",
        revision="dfc4776eacea43ff9f528d75eca3e5f490ed9399",
    )
    assert {lane.id for lane in settings.topics["ai-engineering"].coverage} == {
        "global-english",
        "japan",
        "china",
    }


def test_unknown_source_has_clear_error(tmp_path: Path) -> None:
    (tmp_path / "topics").mkdir()
    (tmp_path / "sources.yaml").write_text("sources: []\n")
    topic = yaml.safe_load(Path("config/topics/ai-engineering.yaml").read_text())
    (tmp_path / "topics/topic.yaml").write_text(yaml.safe_dump(topic))
    with pytest.raises(ConfigurationError, match="unknown sources"):
        load_settings(tmp_path)


def test_changing_existing_topic_version_is_rejected(tmp_path: Path) -> None:
    configured = load_settings(Path("config"), tmp_path / "db.sqlite3")
    store = Store(configured.database_path)
    store.initialize()
    topic = configured.topics["ai-engineering"]
    with store.transaction() as connection:
        persist_topic(connection, topic)
        changed = topic.model_copy(update={"description": "changed without a version bump"})
        with pytest.raises(ValueError, match="increment its version"):
            persist_topic(connection, changed)


def test_html_source_must_remain_disabled_until_adapter_exists() -> None:
    with pytest.raises(ValidationError, match="HTML sources must remain disabled"):
        SourceConfig.model_validate(
            {
                "id": "example-html",
                "name": "Example HTML",
                "kind": "html",
                "url": "https://example.com/news",
                "languages": ["en"],
                "markets": ["global"],
                "attribution": "Example",
            }
        )


def test_bluesky_source_requires_matching_search_configuration() -> None:
    source = {
        "id": "example-bluesky",
        "name": "Example Bluesky",
        "kind": "bluesky_search",
        "url": "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts",
        "languages": ["en"],
        "markets": ["global"],
        "attribution": "Bluesky",
    }
    with pytest.raises(ValidationError, match="require bluesky configuration"):
        SourceConfig.model_validate(source)
    configured = SourceConfig.model_validate(
        source | {"bluesky": {"query": "coding agent", "language": "en"}}
    )
    assert configured.bluesky is not None
    assert configured.bluesky.sort == "latest"


def test_preferred_languages_preserve_order_and_reject_duplicates() -> None:
    configured = UserSettings.model_validate({"preferred_languages": ["en", "ja"]})
    assert configured.preferred_languages == ["en", "ja"]
    with pytest.raises(ValidationError, match="must not contain duplicates"):
        UserSettings.model_validate({"preferred_languages": ["en", "en"]})


def test_llm_model_is_optional_but_cannot_be_blank() -> None:
    assert UserSettings.model_validate({}).llm.model is None
    with pytest.raises(ValidationError, match="at least 1 character"):
        LLMSettings.model_validate({"model": ""})


def test_remote_gguf_requires_llama_provider_and_immutable_revision() -> None:
    model = {
        "repository": "publisher/model-GGUF",
        "filename": "model.gguf",
        "revision": "a" * 40,
    }
    configured = LLMSettings.model_validate({"provider": "llama_cpp", "model": model})
    assert isinstance(configured.model, HuggingFaceModel)
    with pytest.raises(ValidationError, match="requires the llama_cpp provider"):
        LLMSettings.model_validate({"provider": "mlx", "model": model})
    with pytest.raises(ValidationError, match="String should match pattern"):
        LLMSettings.model_validate({"provider": "llama_cpp", "model": model | {"revision": "main"}})
    with pytest.raises(ValidationError, match="relative repository path"):
        LLMSettings.model_validate(
            {"provider": "llama_cpp", "model": model | {"filename": "../model.gguf"}}
        )
