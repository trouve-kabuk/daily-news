from __future__ import annotations

import re
from typing import Protocol


class InferenceError(RuntimeError):
    """A provider-neutral local inference failure."""


class TextInference(Protocol):
    @property
    def model_version(self) -> str: ...

    def prepare(self) -> None: ...

    def translate(self, text: str, source_language: str, target_language: str = "en") -> str: ...

    def summarize(self, text: str, target_language: str = "en") -> str: ...


SUMMARY_MAX_TOKENS = 512
_WORD_COUNT_METADATA = re.compile(
    r"\s*(?:\(\s*)?word count\s*:\s*\d+(?:\s+words?)?\s*\)?\s*",
    re.IGNORECASE,
)
_REASONING_OPENER = re.compile(
    r"^\s*(?:<think>|here(?:'s| is) (?:a |the )?thinking process\s*:|"
    r"thinking process\s*:|reasoning\s*:|1\.\s+\*\*(?:analy|understand|identify))",
    re.IGNORECASE,
)


def without_reasoning_trace(text: str) -> str:
    """Remove a delimited trace or reject an undelimited reasoning response."""
    result = text.strip()
    closing_tag = result.lower().rfind("</think>")
    if closing_tag >= 0:
        final_answer = result[closing_tag + len("</think>") :].strip()
        if final_answer:
            return final_answer
        raise InferenceError("generation returned reasoning without a final answer")
    if _REASONING_OPENER.search(result):
        raise InferenceError("generation returned a reasoning trace instead of a final answer")
    return result


def without_incomplete_trailing_sentence(text: str) -> str:
    """Return only complete sentences when generation stops partway through one."""
    summary = _WORD_COUNT_METADATA.sub(" ", without_reasoning_trace(text)).strip()
    if re.search(r'[.!?。！？]["\')\]]*$', summary):
        return summary
    endings = list(re.finditer(r'[.!?。！？](?=["\')\]]*(?:\s|$))', summary))
    if not endings:
        raise InferenceError("summarization stopped before completing a sentence")
    ending = endings[-1].end()
    while ending < len(summary) and summary[ending] in "\"')]":
        ending += 1
    return summary[:ending].strip()


def translation_instruction(text: str, source_language: str, target_language: str) -> str:
    return (
        f"Translate the following {source_language} text faithfully into {target_language}. "
        "Return only the translation; do not summarize or add commentary.\n\n"
        f"{text}"
    )


def summary_instruction(text: str, target_language: str) -> str:
    return (
        "Write a concise, factual summary of the article below in at most three "
        f"sentences, using {target_language}. Focus on what changed, why it matters, "
        "and the most important evidence. Do not add facts, opinions, headings, or "
        "bullet points. Return only the summary, with no meta-commentary about your "
        "response, and finish every sentence.\n\n"
        f"{text[:40_000]}"
    )
