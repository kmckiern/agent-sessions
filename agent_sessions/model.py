"""
Data models used across Agent Sessions components.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from .util import strip_private_use


@dataclass
class Message:
    """Represents a single chat message."""

    role: str
    content: str
    created_at: datetime | None = None


NormalizedRole = Literal["system", "user", "assistant", "tool"]
NormalizedPartKind = Literal["text", "code", "tool-call", "tool-result"]


@dataclass(slots=True)
class NormalizedPart:
    kind: NormalizedPartKind
    text: str | None = None
    language: str | None = None
    tool_name: str | None = None
    arguments: Any | None = None
    output: Any | None = None
    id: str | None = None


@dataclass(slots=True)
class NormalizedMessage:
    id: str
    role: NormalizedRole
    parts: list[NormalizedPart]
    name: str | None = None
    timestamp: datetime | None = None
    latency_ms: float | None = None
    provider_meta: dict[str, Any] | None = None


@dataclass(slots=True)
class NormalizationDiagnostics:
    total_events: int = 0
    parsed_events: int = 0
    skipped_events: int = 0
    warnings: list[str] = field(default_factory=list)


@dataclass
class SessionRecord:
    """Aggregated data for a session file."""

    provider: str
    session_id: str
    source_path: Path
    started_at: datetime | None
    updated_at: datetime | None
    working_dir: str | None
    model: str | None = None
    messages: list[Message] = field(default_factory=list)
    normalized_messages: list[NormalizedMessage] = field(default_factory=list)
    normalization_diagnostics: NormalizationDiagnostics | None = None
    search_index: SessionSearchIndex = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        self.refresh_search_index()

    @property
    def first_message(self) -> Message | None:
        return self.messages[0] if self.messages else None

    @property
    def last_message(self) -> Message | None:
        return self.messages[-1] if self.messages else None

    @property
    def message_count(self) -> int:
        return len(self.messages)

    def refresh_search_index(self) -> SessionSearchIndex:
        index = SessionSearchIndex.from_session(self)
        object.__setattr__(self, "search_index", index)
        return index


@dataclass(slots=True)
class SessionSearchIndex:
    provider: str
    session_id: str
    model: str
    working_dir: str
    messages: tuple[str, ...]

    @classmethod
    def from_session(cls, session: SessionRecord) -> SessionSearchIndex:
        message_blobs: list[str] = []
        if session.normalized_messages:
            for message in session.normalized_messages:
                blob = _normalize_for_search(_flatten_normalized_message(message))
                if blob:
                    message_blobs.append(blob)
        else:
            for message in session.messages:
                blob = _normalize_for_search(message.content)
                if blob:
                    message_blobs.append(blob)

        return cls(
            provider=_normalize_for_search(session.provider),
            session_id=_normalize_for_search(session.session_id),
            model=_normalize_for_search(session.model),
            working_dir=_normalize_for_search(session.working_dir),
            messages=tuple(message_blobs),
        )

    def matches(self, lowered_term: str) -> bool:
        if not lowered_term:
            return True

        for value in (self.provider, self.session_id, self.model, self.working_dir):
            if value and lowered_term in value:
                return True
        for message in self.messages:
            if lowered_term in message:
                return True
        return False


def _normalize_for_search(value: str | None) -> str:
    if not value:
        return ""
    return strip_private_use(value).lower()


def _flatten_normalized_message(message: NormalizedMessage) -> str:
    chunks: list[str] = []
    for part in message.parts:
        if part.kind in {"text", "code"} and part.text:
            chunks.append(part.text)
            continue
        if part.kind == "tool-call":
            name = part.tool_name or "tool"
            args = _safe_json(part.arguments)
            chunks.append(f"[tool-call] {name} {args}".strip())
            continue
        if part.kind == "tool-result":
            name = part.tool_name or "tool"
            out = _safe_json(part.output)
            chunks.append(f"[tool-result] {name} {out}".strip())
            continue
    value = "\n".join(chunk for chunk in chunks if chunk)
    if len(value) > 4000:
        return value[:4000] + "…"
    return value


def _safe_json(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except TypeError:
        return str(value)
