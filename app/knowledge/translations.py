from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime

from app.llm.runtime import TextInference

PROMPT_VERSION = "faithful-translation-v2"


def translated_field(
    connection: sqlite3.Connection,
    *,
    article_id: int,
    field: str,
    text: str | None,
    source_language: str,
    target_language: str = "en",
    inference: TextInference | None,
    progress: Callable[[str], None] | None = None,
) -> str | None:
    report = progress or (lambda _: None)
    if source_language == target_language or not text:
        return text
    digest = hashlib.sha256(text.encode()).hexdigest()
    model_version = inference.model_version if inference else ""
    row = connection.execute(
        """SELECT translated_text FROM translations
           WHERE article_id=? AND field=? AND target_language=? AND input_hash=?
           AND prompt_version=? AND (?='' OR model_version=?) ORDER BY id DESC LIMIT 1""",
        (
            article_id,
            field,
            target_language,
            digest,
            PROMPT_VERSION,
            model_version,
            model_version,
        ),
    ).fetchone()
    if row:
        return str(row["translated_text"])
    if inference is None:
        _record_outcome(
            connection,
            article_id,
            field,
            "deferred",
            "no local translation backend is configured",
            "unconfigured",
        )
        return None
    report(
        f"Translating {field} from {source_language.upper()} to {target_language.upper()} "
        f"with {inference.model_version}…"
    )
    try:
        translated = inference.translate(text, source_language, target_language).strip()
    except Exception as error:
        _record_outcome(
            connection,
            article_id,
            field,
            "failed",
            str(error),
            inference.model_version,
        )
        raise
    if not translated:
        _record_outcome(
            connection,
            article_id,
            field,
            "failed",
            "translation backend returned empty text",
            inference.model_version,
        )
        return None
    connection.execute(
        """INSERT OR IGNORE INTO translations
           (article_id, field, source_language, target_language, input_hash,
            model_version, prompt_version, translated_text, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            article_id,
            field,
            source_language,
            target_language,
            digest,
            inference.model_version,
            PROMPT_VERSION,
            translated,
            datetime.now(UTC).isoformat(),
        ),
    )
    return translated


def _record_outcome(
    connection: sqlite3.Connection,
    article_id: int,
    field: str,
    status: str,
    reason: str,
    processor_version: str,
) -> None:
    connection.execute(
        """INSERT INTO processing_outcomes
           (article_id, stage, field, status, reason, processor_version, created_at)
           VALUES (?, 'translation', ?, ?, ?, ?, ?)""",
        (
            article_id,
            field,
            status,
            reason,
            processor_version,
            datetime.now(UTC).isoformat(),
        ),
    )
