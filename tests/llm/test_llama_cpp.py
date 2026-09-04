from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest

from app.config import HuggingFaceModel
from app.llm.llama_cpp import DEFAULT_CONTEXT_SIZE, LlamaCppInference
from app.llm.runtime import SUMMARY_MAX_TOKENS, InferenceError


def test_model_loading_uses_full_gpu_offload(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_llama_cpp = ModuleType("llama_cpp")
    load_options: dict[str, object] = {}

    class FakeLlama:
        def __init__(self, **options: object) -> None:
            load_options.update(options)

    fake_llama_cpp.Llama = FakeLlama  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp)

    inference = LlamaCppInference("model.gguf")
    inference.prepare()

    assert load_options == {
        "model_path": str(Path("model.gguf").resolve()),
        "n_ctx": DEFAULT_CONTEXT_SIZE,
        "n_gpu_layers": -1,
        "verbose": False,
    }
    assert inference.model_version == f"llama.cpp:file:{Path('model.gguf').resolve()}"


def test_remote_model_is_downloaded_to_owned_cache_and_has_stable_version(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_llama_cpp = ModuleType("llama_cpp")
    fake_huggingface = ModuleType("huggingface_hub")
    download_options: dict[str, object] = {}
    load_options: dict[str, object] = {}
    downloaded_path = tmp_path / "snapshots" / "model.gguf"

    class FakeLlama:
        def __init__(self, **options: object) -> None:
            load_options.update(options)

    def fake_download(**options: object) -> str:
        download_options.update(options)
        return str(downloaded_path)

    fake_llama_cpp.Llama = FakeLlama  # type: ignore[attr-defined]
    fake_huggingface.hf_hub_download = fake_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_huggingface)
    source = HuggingFaceModel(
        repository="publisher/model-GGUF",
        filename="model-Q4_K_M.gguf",
        revision="a" * 40,
    )

    inference = LlamaCppInference(source, cache_dir=tmp_path / "cache")
    inference.prepare()

    assert download_options == {
        "repo_id": "publisher/model-GGUF",
        "filename": "model-Q4_K_M.gguf",
        "revision": "a" * 40,
        "cache_dir": tmp_path / "cache",
    }
    assert load_options["model_path"] == str(downloaded_path)
    assert inference.model_version == (
        f"llama.cpp:hf:publisher/model-GGUF@{'a' * 40}/model-Q4_K_M.gguf"
    )


def test_remote_download_failure_is_wrapped(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_llama_cpp = ModuleType("llama_cpp")
    fake_huggingface = ModuleType("huggingface_hub")
    fake_llama_cpp.Llama = object  # type: ignore[attr-defined]

    def fail_download(**options: object) -> str:
        raise OSError("network unavailable")

    fake_huggingface.hf_hub_download = fail_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp)
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_huggingface)
    source = HuggingFaceModel(
        repository="publisher/model-GGUF",
        filename="model.gguf",
        revision="b" * 40,
    )

    with pytest.raises(InferenceError, match="Unable to download GGUF.*network unavailable"):
        LlamaCppInference(source, cache_dir=tmp_path).prepare()


def test_model_load_failure_is_wrapped(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_llama_cpp = ModuleType("llama_cpp")

    class FakeLlama:
        def __init__(self, **options: object) -> None:
            raise ValueError("not a GGUF file")

    fake_llama_cpp.Llama = FakeLlama  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp)

    with pytest.raises(InferenceError, match="Unable to load llama.cpp model.*not a GGUF"):
        LlamaCppInference("broken.gguf").prepare()


def test_summary_uses_chat_completion_and_shared_output_policy(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_llama_cpp = ModuleType("llama_cpp")
    completion_options: dict[str, object] = {}

    class FakeLlama:
        def __init__(self, **options: object) -> None:
            pass

        def create_chat_completion(self, **options: object) -> object:
            completion_options.update(options)
            return {
                "choices": [
                    {
                        "message": {
                            "content": "<think>Analyze.</think>A complete summary. Incomplete"
                        }
                    }
                ]
            }

    fake_llama_cpp.Llama = FakeLlama  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp)

    assert LlamaCppInference("model.gguf").summarize("Article text") == "A complete summary."
    assert completion_options["max_tokens"] == SUMMARY_MAX_TOKENS
    assert completion_options["temperature"] == 0
    messages = completion_options["messages"]
    assert isinstance(messages, list)
    assert "Article text" in messages[0]["content"]


def test_translation_returns_only_final_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_llama_cpp = ModuleType("llama_cpp")

    class FakeLlama:
        def __init__(self, **options: object) -> None:
            pass

        def create_chat_completion(self, **options: object) -> object:
            return {
                "choices": [{"message": {"content": "<think>Translate.</think>Clean translation."}}]
            }

    fake_llama_cpp.Llama = FakeLlama  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp)

    assert LlamaCppInference("model.gguf").translate("Texte source", "fr") == ("Clean translation.")


def test_invalid_chat_completion_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_llama_cpp = ModuleType("llama_cpp")

    class FakeLlama:
        def __init__(self, **options: object) -> None:
            pass

        def create_chat_completion(self, **options: object) -> object:
            return {"choices": []}

    fake_llama_cpp.Llama = FakeLlama  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "llama_cpp", fake_llama_cpp)

    with pytest.raises(InferenceError, match="invalid chat completion"):
        LlamaCppInference("model.gguf").summarize("Article text")
