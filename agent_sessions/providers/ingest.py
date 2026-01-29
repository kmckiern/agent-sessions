"""
Shared ingestion helpers for session providers.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..model import Message, NormalizationDiagnostics, NormalizedMessage, SessionRecord
from ..normalize import render_legacy_content
from .logging import debug_warning


class JsonlReader:
    """Iterate JSONL files with resilient decoding."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def __iter__(self) -> Iterator[dict]:
        try:
            with self.path.open("r", encoding="utf-8") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        payload = json.loads(line)
                    except json.JSONDecodeError as exc:
                        debug_warning(f"Discarding invalid JSON in {self.path}", exc)
                        continue
                    if isinstance(payload, dict):
                        yield payload
        except OSError as exc:
            debug_warning(f"Unable to read JSONL file {self.path}", exc)
            return


def iter_paths(base_dir: Path, patterns: Sequence[str]) -> Iterator[Path]:
    """Yield unique paths for the given glob patterns."""

    seen: set[Path] = set()
    for pattern in patterns:
        for path in sorted(base_dir.glob(pattern)):
            if not path.is_file():
                continue
            if path in seen:
                continue
            seen.add(path)
            yield path


@dataclass
class SessionBuilder:
    """Utility to accumulate session metadata consistently across providers."""

    provider: str
    source_path: Path
    session_id: str | None = None
    working_dir: str | None = None
    model: str | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None
    normalization_diagnostics: NormalizationDiagnostics | None = None
    _messages: list[tuple[int, Message]] = field(default_factory=list, init=False)
    _message_keys: set[tuple[str, str, str | None]] = field(default_factory=set, init=False)
    _normalized_messages: list[tuple[int, NormalizedMessage]] = field(
        default_factory=list, init=False
    )
    _normalized_keys: set[tuple[str, str, str | None]] = field(default_factory=set, init=False)
    _model_priority: int = field(default=-1, init=False)

    def set_session_id(self, value: str | None) -> None:
        if value is None:
            return
        candidate = value.strip()
        if candidate:
            self.session_id = candidate

    def record_timestamp(self, timestamp: datetime | None) -> None:
        if not isinstance(timestamp, datetime):
            return
        if self.started_at is None or timestamp < self.started_at:
            self.started_at = timestamp
        if self.updated_at is None or timestamp > self.updated_at:
            self.updated_at = timestamp

    def set_working_dir(self, candidate: str | None) -> None:
        if self.working_dir:
            return
        if isinstance(candidate, str):
            value = candidate.strip()
            if value:
                self.working_dir = value

    def set_model(self, candidate: str | None, priority: int = 0) -> None:
        if not isinstance(candidate, str):
            return
        value = candidate.strip()
        if not value:
            return
        if priority >= self._model_priority:
            self.model = value
            self._model_priority = priority

    def add_message(
        self,
        role: str | None,
        content: str | None,
        created_at: datetime | None = None,
        *,
        dedupe_key: tuple[str, str, str | None] | None = None,
    ) -> Message | None:
        text = (content or "").strip()
        message_role = (role or "").strip() or "event"
        if not text and not message_role:
            return None

        key = dedupe_key or (
            message_role,
            text,
            created_at.isoformat() if isinstance(created_at, datetime) else None,
        )
        if key in self._message_keys:
            return None
        self._message_keys.add(key)

        message = Message(role=message_role, content=text, created_at=created_at)
        order_index = len(self._messages)
        self._messages.append((order_index, message))
        if created_at:
            self.record_timestamp(created_at)
        return message

    def add_normalized_message(
        self,
        message: NormalizedMessage,
        *,
        dedupe_key: tuple[str, str, str | None] | None = None,
    ) -> NormalizedMessage | None:
        if not message.parts and not message.role:
            return None

        content_key = render_legacy_content(message)
        key = dedupe_key or (
            message.role,
            content_key,
            message.timestamp.isoformat() if isinstance(message.timestamp, datetime) else None,
        )
        if key in self._normalized_keys:
            return None
        self._normalized_keys.add(key)

        order_index = len(self._normalized_messages)
        self._normalized_messages.append((order_index, message))
        if message.timestamp:
            self.record_timestamp(message.timestamp)
        return message

    def ingest_record(self, record: SessionRecord, priority: int = 0) -> None:
        if record.started_at:
            self.record_timestamp(record.started_at)
        if record.updated_at:
            self.record_timestamp(record.updated_at)
        if record.normalization_diagnostics:
            self._merge_diagnostics(record.normalization_diagnostics)
        if not self.working_dir:
            self.set_working_dir(record.working_dir)
        if record.model:
            self.set_model(record.model, priority=priority)
        for normalized in record.normalized_messages:
            key = (
                normalized.role,
                render_legacy_content(normalized),
                normalized.timestamp.isoformat() if normalized.timestamp else None,
            )
            self.add_normalized_message(normalized, dedupe_key=key)
        for message in record.messages:
            key = (
                message.role,
                message.content,
                message.created_at.isoformat() if message.created_at else None,
            )
            self.add_message(
                message.role,
                message.content,
                message.created_at,
                dedupe_key=key,
            )

    def build(self, *, session_id: str | None = None) -> SessionRecord | None:
        final_session_id = session_id or self.session_id
        if not final_session_id:
            final_session_id = self.source_path.stem

        if (
            not self._messages
            and self.started_at is None
            and self.updated_at is None
            and not self.model
        ):
            return None

        sorted_normalized = sorted(
            self._normalized_messages,
            key=lambda item: (
                (
                    item[1].timestamp.timestamp()
                    if isinstance(item[1].timestamp, datetime)
                    else float("-inf")
                ),
                item[0],
            ),
        )
        normalized_messages = [message for _, message in sorted_normalized]

        sorted_messages = sorted(
            self._messages,
            key=lambda item: (
                (
                    item[1].created_at.timestamp()
                    if isinstance(item[1].created_at, datetime)
                    else float("-inf")
                ),
                item[0],
            ),
        )
        messages = [message for _, message in sorted_messages]

        if not messages and normalized_messages:
            messages = [
                Message(
                    role=normalized.role,
                    content=render_legacy_content(normalized),
                    created_at=normalized.timestamp,
                )
                for normalized in normalized_messages
            ]

        return SessionRecord(
            provider=self.provider,
            session_id=final_session_id,
            source_path=self.source_path,
            started_at=self.started_at,
            updated_at=self.updated_at,
            working_dir=self.working_dir,
            model=self.model,
            messages=messages,
            normalized_messages=normalized_messages,
            normalization_diagnostics=self.normalization_diagnostics,
        )

    def _merge_diagnostics(self, incoming: NormalizationDiagnostics) -> None:
        if self.normalization_diagnostics is None:
            self.normalization_diagnostics = NormalizationDiagnostics()
        diag = self.normalization_diagnostics
        diag.total_events += incoming.total_events
        diag.parsed_events += incoming.parsed_events
        diag.skipped_events += incoming.skipped_events
        if incoming.warnings:
            diag.warnings.extend(incoming.warnings)


def merge_session_records(primary: SessionRecord, incoming: SessionRecord) -> SessionRecord:
    """
    Combine two records while deduplicating messages.

    The primary record always wins for identifiers; timestamps and models are
    merged so we keep the earliest start, latest update, and prefer newer model
    metadata. Messages are deduped by role/content/timestamp to avoid repeats
    when multiple providers surface the same events.
    """

    builder = SessionBuilder(
        provider=primary.provider,
        source_path=primary.source_path,
        session_id=primary.session_id,
        working_dir=primary.working_dir,
        model=primary.model,
    )
    builder.record_timestamp(primary.started_at)
    builder.record_timestamp(primary.updated_at)
    builder.ingest_record(primary, priority=1)
    builder.ingest_record(incoming, priority=2)
    # Fallback timestamps if missing on primary
    builder.record_timestamp(incoming.started_at)
    builder.record_timestamp(incoming.updated_at)
    if not builder.working_dir:
        builder.set_working_dir(incoming.working_dir)
    return builder.build(session_id=primary.session_id) or primary
