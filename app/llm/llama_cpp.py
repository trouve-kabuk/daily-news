from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from app.config import HuggingFaceModel, LocalModel
from app.llm.runtime import (
    SUMMARY_MAX_TOKENS,
    InferenceError,
    summary_instruction,
    translation_instruction,
    without_incomplete_trailing_sentence,
    without_reasoning_trace,
)

DEFAULT_CONTEXT_SIZE = 16_384


def default_model_cache_dir() -> Path:
    root = os.environ.get("XDG_CACHE_HOME")
    return (Path(root) if root else Path.home() / ".cache") / "daily-news" / "models"


class LlamaCppInference:
    """Lazy llama.cpp backend for a local or revision-pinned GGUF model."""

    def __init__(self, model: LocalModel | Path, cache_dir: Path | None = None) -> None:
        self.model = model
        self.cache_dir = cache_dir or default_model_cache_dir()
        self._model: Any = None

    def _local_model_path(self) -> Path:
        if not isinstance(self.model, HuggingFaceModel):
            return Path(self.model).expanduser().resolve()
        try:
            from huggingface_hub import hf_hub_download
        except ImportError as error:
            raise InferenceError(
                "huggingface-hub is unavailable; install the llama optional dependency"
            ) from error
        try:
            downloaded = hf_hub_download(
                repo_id=self.model.repository,
                filename=self.model.filename,
                revision=self.model.revision,
                cache_dir=self.cache_dir,
            )
        except Exception as error:
            raise InferenceError(
                f"Unable to download GGUF {self.model.repository!r}/{self.model.filename!r} "
                f"at revision {self.model.revision!r}: {error}"
            ) from error
        return Path(downloaded)

    @property
    def model_version(self) -> str:
        if isinstance(self.model, HuggingFaceModel):
            return (
                f"llama.cpp:hf:{self.model.repository}@{self.model.revision}/{self.model.filename}"
            )
        return f"llama.cpp:file:{Path(self.model).expanduser().resolve()}"

    def _load(self) -> None:
        if self._model is not None:
            return
        try:
            from llama_cpp import Llama
        except ImportError as error:
            raise InferenceError(
                "llama-cpp-python is unavailable; install the llama optional dependency"
            ) from error
        model_path = self._local_model_path()
        try:
            self._model = Llama(
                model_path=str(model_path),
                n_ctx=DEFAULT_CONTEXT_SIZE,
                n_gpu_layers=-1,
                verbose=False,
            )
        except Exception as error:
            raise InferenceError(
                f"Unable to load llama.cpp model {str(model_path)!r}: {error}. "
                "Check the llm.model settings and that the artifact is a supported GGUF file."
            ) from error

    def prepare(self) -> None:
        self._load()

    def _generate(self, instruction: str, max_tokens: int) -> str:
        self._load()
        response = self._model.create_chat_completion(
            messages=[{"role": "user", "content": instruction}],
            max_tokens=max_tokens,
            temperature=0,
        )
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as error:
            raise InferenceError("llama.cpp returned an invalid chat completion") from error
        if not isinstance(content, str) or not content.strip():
            raise InferenceError("llama.cpp returned an empty chat completion")
        return content.strip()

    def translate(self, text: str, source_language: str, target_language: str = "en") -> str:
        try:
            generated = self._generate(
                translation_instruction(text, source_language, target_language),
                max(128, min(4096, len(text) * 2)),
            )
            return without_reasoning_trace(generated)
        except Exception as error:
            if isinstance(error, InferenceError):
                raise
            raise InferenceError(f"llama.cpp translation failed: {error}") from error

    def summarize(self, text: str, target_language: str = "en") -> str:
        try:
            generated = self._generate(
                summary_instruction(text, target_language), SUMMARY_MAX_TOKENS
            )
            return without_incomplete_trailing_sentence(generated)
        except Exception as error:
            if isinstance(error, InferenceError):
                raise
            raise InferenceError(f"llama.cpp summarization failed: {error}") from error
