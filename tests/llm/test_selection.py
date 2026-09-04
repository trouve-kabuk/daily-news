from __future__ import annotations

from app.cli import _local_inference
from app.config import HuggingFaceModel, LLMSettings
from app.llm.llama_cpp import LlamaCppInference
from app.llm.mlx import MLXInference


def test_no_configured_model_returns_none() -> None:
    assert _local_inference(LLMSettings()) is None


def test_llama_cpp_model_selects_llama_backend() -> None:
    config = LLMSettings(provider="llama_cpp", model="model.gguf")
    assert isinstance(_local_inference(config), LlamaCppInference)


def test_remote_llama_cpp_model_selects_llama_backend() -> None:
    config = LLMSettings(
        provider="llama_cpp",
        model=HuggingFaceModel(
            repository="publisher/model-GGUF",
            filename="model.gguf",
            revision="a" * 40,
        ),
    )
    assert isinstance(_local_inference(config), LlamaCppInference)


def test_mlx_model_selects_mlx_backend() -> None:
    config = LLMSettings(provider="mlx", model="repository/model")
    assert isinstance(_local_inference(config), MLXInference)
