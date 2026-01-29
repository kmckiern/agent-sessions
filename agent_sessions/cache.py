"""
Disk cache for parsed session records.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .model import (
    Message,
    NormalizationDiagnostics,
    NormalizedMessage,
    NormalizedPart,
    SessionRecord,
)
from .util import parse_timestamp

CACHE_VERSION = 1


def default_cache_dir() -> Path:
    xdg_cache = os.getenv("XDG_CACHE_HOME")
    if xdg_cache:
        return Path(xdg_cache) / "agent-sessions"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "agent-sessions"
    return Path.home() / ".cache" / "agent-sessions"


def cache_disabled() -> bool:
    return os.getenv("AGENT_SESSIONS_DISABLE_DISK_CACHE", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def cache_dir_from_env() -> Path:
    value = os.getenv("AGENT_SESSIONS_CACHE_DIR", "").strip()
    if value:
        return Path(value).expanduser()
    return default_cache_dir()


class DiskSessionCache:
    def __init__(self, cache_dir: Path, *, enabled: bool = True) -> None:
        self.enabled = enabled
        self.cache_dir = cache_dir
        self.cache_path = cache_dir / "session_cache.json"
        self._entries: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_env(cls) -> DiskSessionCache:
        if cache_disabled():
            return cls(Path("."), enabled=False)
        return cls(cache_dir_from_env(), enabled=True)

    def load(self) -> None:
        if not self.enabled:
            return
        try:
            payload = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if payload.get("version") != CACHE_VERSION:
            return
        entries = payload.get("entries")
        if isinstance(entries, list):
            self._entries = {
                self._entry_key(item.get("provider"), item.get("source_path")): item
                for item in entries
                if isinstance(item, dict)
                and isinstance(item.get("provider"), str)
                and isinstance(item.get("source_path"), str)
            }

    def lookup(self, provider: str, path: Path) -> SessionRecord | None:
        if not self.enabled:
            return None
        stat = self._stat_path(path)
        if stat is None:
            return None
        key = self._entry_key(provider, str(path))
        entry = self._entries.get(key)
        if not entry:
            return None
        if entry.get("mtime_ns") != stat.st_mtime_ns or entry.get("size") != stat.st_size:
            return None
        session = entry.get("session")
        if not isinstance(session, dict):
            return None
        return self._deserialize_session(session)

    def store(self, provider: str, path: Path, record: SessionRecord) -> None:
        if not self.enabled:
            return
        stat = self._stat_path(path)
        if stat is None:
            return
        key = self._entry_key(provider, str(path))
        self._entries[key] = {
            "provider": provider,
            "source_path": str(path),
            "mtime_ns": stat.st_mtime_ns,
            "size": stat.st_size,
            "session": self._serialize_session(record),
        }

    def persist(self) -> None:
        if not self.enabled:
            return
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": CACHE_VERSION,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "entries": list(self._entries.values()),
            }
            tmp_path = self.cache_path.with_suffix(".tmp")
            tmp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            tmp_path.replace(self.cache_path)
        except OSError:
            # Disk cache is best-effort; keep the in-memory cache usable even
            # when the configured cache directory is unwritable.
            self.enabled = False

    @staticmethod
    def _stat_path(path: Path) -> os.stat_result | None:
        try:
            return path.stat()
        except OSError:
            return None

    @staticmethod
    def _entry_key(provider: str | None, source_path: str | None) -> str:
        return f"{provider}::{source_path}"

    @staticmethod
    def _serialize_datetime(value: datetime | None) -> str | None:
        if not value:
            return None
        return value.isoformat()

    def _serialize_session(self, record: SessionRecord) -> dict[str, Any]:
        return {
            "provider": record.provider,
            "session_id": record.session_id,
            "source_path": str(record.source_path),
            "started_at": self._serialize_datetime(record.started_at),
            "updated_at": self._serialize_datetime(record.updated_at),
            "working_dir": record.working_dir,
            "model": record.model,
            "messages": [
                {
                    "role": message.role,
                    "content": message.content,
                    "created_at": self._serialize_datetime(message.created_at),
                }
                for message in record.messages
            ],
            "normalized_messages": [
                {
                    "id": msg.id,
                    "role": msg.role,
                    "name": msg.name,
                    "timestamp": self._serialize_datetime(msg.timestamp),
                    "latency_ms": msg.latency_ms,
                    "provider_meta": _json_friendly(msg.provider_meta),
                    "parts": [
                        {
                            "kind": part.kind,
                            "text": part.text,
                            "language": part.language,
                            "tool_name": part.tool_name,
                            "arguments": _json_friendly(part.arguments),
                            "output": _json_friendly(part.output),
                            "id": part.id,
                        }
                        for part in msg.parts
                    ],
                }
                for msg in (record.normalized_messages or [])
            ],
            "normalization_diagnostics": (
                {
                    "total_events": record.normalization_diagnostics.total_events,
                    "parsed_events": record.normalization_diagnostics.parsed_events,
                    "skipped_events": record.normalization_diagnostics.skipped_events,
                    "warnings": list(record.normalization_diagnostics.warnings or []),
                }
                if record.normalization_diagnostics
                else None
            ),
        }

    def _deserialize_session(self, payload: dict[str, Any]) -> SessionRecord:
        messages = []
        for entry in payload.get("messages") or []:
            if not isinstance(entry, dict):
                continue
            messages.append(
                Message(
                    role=str(entry.get("role") or "event"),
                    content=str(entry.get("content") or ""),
                    created_at=parse_timestamp(entry.get("created_at")),
                )
            )

        normalized_messages: list[NormalizedMessage] = []
        for entry in payload.get("normalized_messages") or []:
            if not isinstance(entry, dict):
                continue
            parts: list[NormalizedPart] = []
            for part_entry in entry.get("parts") or []:
                if not isinstance(part_entry, dict):
                    continue
                kind = str(part_entry.get("kind") or "text")
                parts.append(
                    NormalizedPart(
                        kind=kind,  # type: ignore[arg-type]
                        text=part_entry.get("text"),
                        language=part_entry.get("language"),
                        tool_name=part_entry.get("tool_name"),
                        arguments=part_entry.get("arguments"),
                        output=part_entry.get("output"),
                        id=part_entry.get("id"),
                    )
                )
            normalized_messages.append(
                NormalizedMessage(
                    id=str(entry.get("id") or ""),
                    role=str(entry.get("role") or "assistant"),  # type: ignore[arg-type]
                    name=entry.get("name"),
                    timestamp=parse_timestamp(entry.get("timestamp")),
                    latency_ms=(
                        float(latency_val)
                        if isinstance((latency_val := entry.get("latency_ms")), int | float)
                        else None
                    ),
                    provider_meta=entry.get("provider_meta"),
                    parts=parts,
                )
            )

        diagnostics_payload = payload.get("normalization_diagnostics")
        diagnostics: NormalizationDiagnostics | None = None
        if isinstance(diagnostics_payload, dict):
            diagnostics = NormalizationDiagnostics(
                total_events=int(diagnostics_payload.get("total_events") or 0),
                parsed_events=int(diagnostics_payload.get("parsed_events") or 0),
                skipped_events=int(diagnostics_payload.get("skipped_events") or 0),
                warnings=[
                    str(item)
                    for item in (diagnostics_payload.get("warnings") or [])
                    if item is not None
                ],
            )

        return SessionRecord(
            provider=str(payload.get("provider") or ""),
            session_id=str(payload.get("session_id") or ""),
            source_path=Path(str(payload.get("source_path") or "")),
            started_at=parse_timestamp(payload.get("started_at")),
            updated_at=parse_timestamp(payload.get("updated_at")),
            working_dir=payload.get("working_dir"),
            model=payload.get("model"),
            messages=messages,
            normalized_messages=normalized_messages,
            normalization_diagnostics=diagnostics,
        )


def _json_friendly(value: Any) -> Any:
    if value is None:
        return None
    try:
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return value
    except TypeError:
        return str(value)
