from __future__ import annotations

import os
from typing import Any

from app.llm.runtime import (
    SUMMARY_MAX_TOKENS,
    InferenceError,
    summary_instruction,
    translation_instruction,
    without_incomplete_trailing_sentence,
    without_reasoning_trace,
)

RECOMMENDED_MODEL = "mlx-community/Qwen3.6-35B-A3B-4bit"
LOW_MEMORY_MODEL = "mlx-community/Qwen3-4B-Instruct-2507-4bit"
MODEL_ALIASES = {
    "mlx-community/Qwen3-4B-Instruct-4bit": LOW_MEMORY_MODEL,
}


def _instruction_prompt(tokenizer: Any, instruction: str) -> str:
    """Apply a model's instruction template when its tokenizer provides one."""
    apply_template = getattr(tokenizer, "apply_chat_template", None)
    if not callable(apply_template):
        return instruction
    return str(
        apply_template(
            [{"role": "user", "content": instruction}],
            tokenize=False,
            add_generation_prompt=True,
            enable_thinking=False,
        )
    )


# Preserve the private names used by callers from before output policy became shared.
_without_reasoning_trace = without_reasoning_trace
_without_incomplete_trailing_sentence = without_incomplete_trailing_sentence


class MLXInference:
    """Lazy MLX-LM translation backend for supported Apple Silicon hosts."""

    def __init__(self, model_name: str) -> None:
        self.model_name = MODEL_ALIASES.get(model_name, model_name)
        self._model: Any = None
        self._tokenizer: Any = None

    @property
    def model_version(self) -> str:
        return self.model_name

    def _load(self) -> None:
        if self._model is not None:
            return
        # Keep third-party download rendering out of the application's CLI.
        os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
        try:
            from huggingface_hub.utils import disable_progress_bars

            disable_progress_bars()
        except ImportError:
            pass
        try:
            from mlx_lm import load
        except ImportError as error:
            raise InferenceError(
                "MLX-LM is unavailable; run on Apple Silicon with the project Conda environment"
            ) from error
        try:
            loaded = load(self.model_name)
            self._model, self._tokenizer = loaded[0], loaded[1]
        except Exception as error:
            raise InferenceError(
                f"Unable to load MLX model {self.model_name!r}: {error}. "
                "Check llm.model in settings.yaml and that the model is accessible. "
                f"A known public option is {RECOMMENDED_MODEL!r}."
            ) from error

    def prepare(self) -> None:
        """Load the model before inference so transports can report that stage."""
        self._load()

    def translate(self, text: str, source_language: str, target_language: str = "en") -> str:
        self._load()
        try:
            from mlx_lm import generate

            instruction = translation_instruction(text, source_language, target_language)
            prompt = _instruction_prompt(self._tokenizer, instruction)
            generated = str(
                generate(
                    self._model,
                    self._tokenizer,
                    prompt=prompt,
                    max_tokens=max(128, min(4096, len(text) * 2)),
                )
            ).strip()
            return _without_reasoning_trace(generated)
        except Exception as error:
            raise InferenceError(f"MLX translation failed: {error}") from error

    def summarize(self, text: str, target_language: str = "en") -> str:
        self._load()
        try:
            from mlx_lm import generate

            instruction = summary_instruction(text, target_language)
            prompt = _instruction_prompt(self._tokenizer, instruction)
            generated = str(
                generate(
                    self._model,
                    self._tokenizer,
                    prompt=prompt,
                    max_tokens=SUMMARY_MAX_TOKENS,
                )
            ).strip()
            return _without_incomplete_trailing_sentence(generated)
        except Exception as error:
            if isinstance(error, InferenceError):
                raise
            raise InferenceError(f"MLX summarization failed: {error}") from error
