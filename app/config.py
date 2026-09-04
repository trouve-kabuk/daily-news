from __future__ import annotations

import hashlib
import json
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

Language = Annotated[str, Field(pattern=r"^[a-z]{2,3}(?:-[A-Z][a-z]{3})?$")]
Market = Annotated[str, Field(pattern=r"^(?:global|[A-Z]{2})$")]


class RetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attempts: int = Field(default=3, ge=1, le=8)
    backoff_seconds: float = Field(default=0.5, ge=0, le=30)


class BlueskySearchConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    query: str = Field(min_length=1, max_length=512)
    sort: Literal["latest", "top"] = "latest"
    language: Language | None = None


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    name: str
    kind: Literal["rss", "atom", "arxiv_api", "hackernews_api", "bluesky_search", "html"]
    url: str
    languages: list[Language]
    markets: list[Market]
    enabled: bool = True
    topic_specific: bool = True
    max_items: int = Field(default=100, ge=1, le=500)
    rate_limit_seconds: float = Field(default=1, ge=0, le=60)
    retry: RetryPolicy = Field(default_factory=RetryPolicy)
    attribution: str
    retain_content: bool = True
    bluesky: BlueskySearchConfig | None = None

    @model_validator(mode="after")
    def enabled_source_has_an_adapter(self) -> SourceConfig:
        if self.enabled and self.kind == "html":
            raise ValueError("HTML sources must remain disabled until an adapter is implemented")
        if self.kind == "bluesky_search" and self.bluesky is None:
            raise ValueError("Bluesky search sources require bluesky configuration")
        if self.kind != "bluesky_search" and self.bluesky is not None:
            raise ValueError("bluesky configuration is only valid for Bluesky search sources")
        if (
            self.bluesky is not None
            and self.bluesky.language is not None
            and self.bluesky.language not in self.languages
        ):
            raise ValueError("Bluesky search language must be one of the source languages")
        return self


class CoverageLane(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    markets: list[Market]
    languages: list[Language]
    target_articles: int | None = Field(default=None, ge=0)
    max_articles: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def target_does_not_exceed_maximum(self) -> CoverageLane:
        if (
            self.target_articles is not None
            and self.max_articles is not None
            and self.target_articles > self.max_articles
        ):
            raise ValueError("target_articles must not exceed max_articles")
        return self


class EditionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_articles: int = Field(ge=1)


class HuggingFaceModel(BaseModel):
    """An immutable GGUF artifact hosted by Hugging Face."""

    model_config = ConfigDict(extra="forbid")
    repository: str = Field(min_length=1)
    filename: str = Field(min_length=1)
    revision: str = Field(pattern=r"^[0-9a-f]{40}$")

    @field_validator("filename")
    @classmethod
    def filename_is_relative(cls, filename: str) -> str:
        path = PurePosixPath(filename)
        if path.is_absolute() or ".." in path.parts:
            raise ValueError("filename must be a relative repository path")
        return filename


LocalModel = Annotated[str, Field(min_length=1)] | HuggingFaceModel


class LLMSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Literal["mlx", "llama_cpp"] = "mlx"
    model: LocalModel | None = None

    @model_validator(mode="after")
    def remote_gguf_requires_llama_cpp(self) -> LLMSettings:
        if self.provider == "mlx" and isinstance(self.model, HuggingFaceModel):
            raise ValueError("a Hugging Face GGUF model requires the llama_cpp provider")
        return self


class UserSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    preferred_languages: list[Language] = Field(default_factory=lambda: ["en"], min_length=1)
    llm: LLMSettings = Field(default_factory=LLMSettings)

    @model_validator(mode="after")
    def languages_are_unique(self) -> UserSettings:
        if len(self.preferred_languages) != len(set(self.preferred_languages)):
            raise ValueError("preferred_languages must not contain duplicates")
        return self


class TopicConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    version: int = Field(ge=1)
    name: str
    description: str
    facets: list[str] = Field(min_length=1)
    positive_examples: list[str] = Field(default_factory=list)
    hard_negative_examples: list[str] = Field(default_factory=list)
    exclusions: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    terms: list[str] = Field(default_factory=list)
    sources: list[str] = Field(min_length=1)
    freshness_hours: int = Field(default=72, ge=1, le=24 * 31)
    edition: EditionConfig
    coverage: list[CoverageLane] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_lanes(self) -> TopicConfig:
        lane_ids = [lane.id for lane in self.coverage]
        if len(lane_ids) != len(set(lane_ids)):
            raise ValueError("coverage lane IDs must be unique")
        return self

    @property
    def definition_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        return hashlib.sha256(payload).hexdigest()


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database_path: Path
    preferred_languages: list[Language]
    llm: LLMSettings
    sources: dict[str, SourceConfig]
    topics: dict[str, TopicConfig]


class ConfigurationError(ValueError):
    """Raised when configuration cannot be loaded consistently."""


def default_config_dir() -> Path:
    packaged = Path(__file__).resolve().parent / "default_config"
    return packaged if packaged.is_dir() else Path(__file__).resolve().parent.parent / "config"


def load_settings(config_dir: Path | None = None, database_path: Path | None = None) -> Settings:
    root = config_dir or default_config_dir()
    try:
        user_settings_path = root / "settings.yaml"
        user_settings_data = (
            yaml.safe_load(user_settings_path.read_text()) or {}
            if user_settings_path.exists()
            else {}
        )
        user_settings = UserSettings.model_validate(user_settings_data)
        source_data = yaml.safe_load((root / "sources.yaml").read_text()) or {}
        source_items = [SourceConfig.model_validate(item) for item in source_data["sources"]]
        sources = {item.id: item for item in source_items}
        if len(sources) != len(source_items):
            raise ConfigurationError("source IDs must be unique")

        topics: dict[str, TopicConfig] = {}
        for topic_path in sorted((root / "topics").glob("*.yaml")):
            topic = TopicConfig.model_validate(yaml.safe_load(topic_path.read_text()))
            if topic.id in topics:
                raise ConfigurationError(f"duplicate topic ID: {topic.id}")
            unknown = set(topic.sources) - sources.keys()
            if unknown:
                raise ConfigurationError(
                    f"topic {topic.id!r} references unknown sources: {sorted(unknown)}"
                )
            topics[topic.id] = topic
        if not topics:
            raise ConfigurationError(f"no topic files found under {root / 'topics'}")
    except (OSError, KeyError, TypeError, ValidationError, yaml.YAMLError) as error:
        raise ConfigurationError(f"invalid configuration in {root}: {error}") from error

    return Settings(
        database_path=database_path or Path("daily-news.sqlite3"),
        preferred_languages=user_settings.preferred_languages,
        llm=user_settings.llm,
        sources=sources,
        topics=topics,
    )
