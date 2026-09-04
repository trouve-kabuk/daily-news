from __future__ import annotations

import os
import sys
from types import ModuleType

import pytest

from app.llm.mlx import (
    LOW_MEMORY_MODEL,
    SUMMARY_MAX_TOKENS,
    MLXInference,
    _without_incomplete_trailing_sentence,
    _without_reasoning_trace,
)
from app.llm.runtime import InferenceError


def test_model_repository_failure_is_wrapped_as_inference_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mlx = ModuleType("mlx_lm")

    def fail_load(model_name: str) -> object:
        raise RuntimeError(f"404 repository not found: {model_name}")

    fake_mlx.load = fail_load  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlx_lm", fake_mlx)

    inference = MLXInference("missing/model")
    with pytest.raises(InferenceError, match="Unable to load MLX model.*missing/model"):
        inference.summarize("Article text")


def test_obsolete_documented_model_name_maps_to_public_repository() -> None:
    inference = MLXInference("mlx-community/Qwen3-4B-Instruct-4bit")
    assert inference.model_version == LOW_MEMORY_MODEL


def test_incomplete_trailing_summary_sentence_is_removed() -> None:
    generated = "The release improves local inference. Its largest benchmark gain was"
    assert _without_incomplete_trailing_sentence(generated) == (
        "The release improves local inference."
    )


def test_word_count_metadata_is_removed_from_summary() -> None:
    generated = "First factual sentence.\n\n(Word Count: 147)\n\nSecond factual sentence."
    assert _without_incomplete_trailing_sentence(generated) == (
        "First factual sentence. Second factual sentence."
    )


def test_delimited_reasoning_is_removed_from_summary() -> None:
    generated = "<think>Analyze the request in steps.</think>Final factual summary."
    assert _without_reasoning_trace(generated) == "Final factual summary."


def test_undelimited_reasoning_is_rejected() -> None:
    generated = "Here's a thinking process:\n\n1. **Analyze User Input:** summarize it."
    with pytest.raises(InferenceError, match="reasoning trace instead of a final answer"):
        _without_reasoning_trace(generated)


def test_entirely_incomplete_summary_is_rejected() -> None:
    with pytest.raises(InferenceError, match="before completing a sentence"):
        _without_incomplete_trailing_sentence("The release improves local inference")


def test_summary_has_larger_generation_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_mlx = ModuleType("mlx_lm")
    generation_options: dict[str, object] = {}
    template_messages: list[dict[str, str]] = []

    class FakeTokenizer:
        def apply_chat_template(
            self,
            messages: list[dict[str, str]],
            *,
            tokenize: bool,
            add_generation_prompt: bool,
            enable_thinking: bool,
        ) -> str:
            template_messages.extend(messages)
            assert tokenize is False
            assert add_generation_prompt is True
            assert enable_thinking is False
            return "templated instruction"

    def load(model_name: str) -> tuple[object, object]:
        return object(), FakeTokenizer()

    def generate(model: object, tokenizer: object, **options: object) -> str:
        generation_options.update(options)
        return "A complete summary."

    fake_mlx.load = load  # type: ignore[attr-defined]
    fake_mlx.generate = generate  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlx_lm", fake_mlx)

    assert MLXInference("test/model").summarize("Article text") == "A complete summary."
    assert generation_options["max_tokens"] == SUMMARY_MAX_TOKENS
    assert generation_options["prompt"] == "templated instruction"
    assert "Article text" in template_messages[0]["content"]
    assert "150 words" not in template_messages[0]["content"]


def test_model_loading_disables_hugging_face_progress_bars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mlx = ModuleType("mlx_lm")
    fake_hub = ModuleType("huggingface_hub")
    fake_hub_utils = ModuleType("huggingface_hub.utils")
    disabled: list[bool] = []

    fake_mlx.load = lambda model_name: (object(), object())  # type: ignore[attr-defined]
    fake_hub_utils.disable_progress_bars = lambda: disabled.append(True)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "mlx_lm", fake_mlx)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    monkeypatch.setitem(sys.modules, "huggingface_hub.utils", fake_hub_utils)
    monkeypatch.delenv("HF_HUB_DISABLE_PROGRESS_BARS", raising=False)

    MLXInference("test/model").prepare()

    assert disabled == [True]
    assert os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] == "1"


def test_translation_uses_non_thinking_template_and_removes_reasoning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_mlx = ModuleType("mlx_lm")
    template_options: dict[str, object] = {}

    class FakeTokenizer:
        def apply_chat_template(self, messages: list[dict[str, str]], **options: object) -> str:
            template_options.update(options)
            return "templated translation"

    fake_mlx.load = lambda model_name: (object(), FakeTokenizer())  # type: ignore[attr-defined]
    fake_mlx.generate = (  # type: ignore[attr-defined]
        lambda model, tokenizer, **options: "<think>Translate carefully.</think>Clean translation."
    )
    monkeypatch.setitem(sys.modules, "mlx_lm", fake_mlx)

    translated = MLXInference("test/model").translate("Texte source", "fr")

    assert translated == "Clean translation."
    assert template_options["enable_thinking"] is False
