"""
Disk cache utilities for parsed session records and derived metadata snapshots.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from .model import (
    Message,
    NormalizationDiagnostics,
    NormalizedMessage,
    NormalizedPart,
    SessionRecord,
)
from .util import parse_timestamp

SESSION_CACHE_VERSION = 1
METADATA_CACHE_VERSION = 1
METADATA_SCHEMA_VERSION = 1
WORKSPACE_CACHE_DIRNAME = ".agent-sessions-cache"

# Backward-compatible alias used by older tests/imports.
CACHE_VERSION = SESSION_CACHE_VERSION

MetadataCacheStatus = Literal[
    "hit",
    "miss",
    "write_fail",
    "fallback_hit",
    "fallback_fail",
]


@dataclass(frozen=True, slots=True)
class MetadataCacheAttempt:
    cache_dir: Path
    cache_path: Path
    outcome: Literal["hit", "miss", "invalid", "error"]
    error_type: str | None = None
    error_message: str | None = None


@dataclass(frozen=True, slots=True)
class MetadataCacheLoadResult:
    status: MetadataCacheStatus
    snapshot: CachedMetadataSnapshot | None
    cache_dir: Path | None
    cache_path: Path | None
    attempts: tuple[MetadataCacheAttempt, ...] = ()


@dataclass(frozen=True, slots=True)
class MetadataCachePersistResult:
    status: MetadataCacheStatus
    cache_dir: Path | None
    cache_path: Path | None
    attempts: tuple[MetadataCacheAttempt, ...] = ()


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


def metadata_cache_dir_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_value = os.getenv("AGENT_SESSIONS_CACHE_DIR", "").strip()
    if env_value:
        candidates.append(Path(env_value).expanduser())
    candidates.append(default_cache_dir())
    candidates.append(Path.cwd() / WORKSPACE_CACHE_DIRNAME)

    unique: list[Path] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            normalized = os.path.normcase(os.fspath(path.expanduser()))
        except OSError:
            normalized = os.path.normcase(os.fspath(path))
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(path)
    return unique


def path_fingerprint(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


@dataclass(frozen=True)
class CachedMetadataSnapshot:
    cache_key: str
    manifest_hash: str
    manifest: dict[tuple[str, str], tuple[int, int]]
    sessions: list[SessionRecord]


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
        payload = _load_json_payload(self.cache_path)
        if not isinstance(payload, dict):
            return
        if payload.get("version") != SESSION_CACHE_VERSION:
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
        fingerprint = path_fingerprint(path)
        if fingerprint is None:
            return None
        key = self._entry_key(provider, str(path))
        entry = self._entries.get(key)
        if not entry:
            return None
        cached_mtime = entry.get("mtime_ns")
        cached_size = entry.get("size")
        if (cached_mtime, cached_size) != fingerprint:
            return None
        session = entry.get("session")
        if not isinstance(session, dict):
            return None
        return deserialize_session_record(session)

    def store(self, provider: str, path: Path, record: SessionRecord) -> None:
        if not self.enabled:
            return
        fingerprint = path_fingerprint(path)
        if fingerprint is None:
            return
        mtime_ns, size = fingerprint
        key = self._entry_key(provider, str(path))
        self._entries[key] = {
            "provider": provider,
            "source_path": str(path),
            "mtime_ns": mtime_ns,
            "size": size,
            "session": serialize_session_record(record),
        }

    def persist(self) -> None:
        if not self.enabled:
            return
        payload = {
            "version": SESSION_CACHE_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "entries": list(self._entries.values()),
        }
        if not _atomic_write_json(self.cache_dir, self.cache_path, payload):
            # Disk cache is best-effort; keep in-memory usage functional.
            self.enabled = False

    @staticmethod
    def _entry_key(provider: str | None, source_path: str | None) -> str:
        return f"{provider}::{source_path}"


class DiskMetadataCache:
    def __init__(
        self,
        cache_dir: Path,
        *,
        enabled: bool = True,
        cache_dirs: list[Path] | None = None,
    ) -> None:
        self.enabled = enabled
        self.cache_dir = cache_dir
        self.cache_path = cache_dir / "metadata_snapshot.json"
        self._cache_dirs = list(cache_dirs) if cache_dirs is not None else [cache_dir]

    @classmethod
    def from_env(cls) -> DiskMetadataCache:
        if cache_disabled():
            return cls(Path("."), enabled=False)
        cache_dirs = metadata_cache_dir_candidates()
        primary_dir = cache_dirs[0]
        return cls(primary_dir, enabled=True, cache_dirs=cache_dirs)

    def load(self, cache_key: str) -> MetadataCacheLoadResult:
        if not self.enabled:
            return MetadataCacheLoadResult(
                status="miss",
                snapshot=None,
                cache_dir=None,
                cache_path=None,
            )

        attempts: list[MetadataCacheAttempt] = []
        saw_failure = False

        for idx, cache_dir in enumerate(self._cache_dirs):
            cache_path = cache_dir / "metadata_snapshot.json"
            payload, error = _load_json_payload_with_error(cache_path)
            if error is not None:
                if isinstance(error, FileNotFoundError):
                    attempts.append(
                        MetadataCacheAttempt(
                            cache_dir=cache_dir,
                            cache_path=cache_path,
                            outcome="miss",
                        )
                    )
                    continue
                saw_failure = True
                attempts.append(
                    MetadataCacheAttempt(
                        cache_dir=cache_dir,
                        cache_path=cache_path,
                        outcome="error",
                        error_type=type(error).__name__,
                        error_message=str(error),
                    )
                )
                continue

            snapshot, outcome, reason = _parse_metadata_snapshot(payload, cache_key)
            if snapshot is None:
                if outcome == "miss":
                    attempts.append(
                        MetadataCacheAttempt(
                            cache_dir=cache_dir,
                            cache_path=cache_path,
                            outcome="miss",
                            error_message=reason,
                        )
                    )
                    continue
                saw_failure = True
                attempts.append(
                    MetadataCacheAttempt(
                        cache_dir=cache_dir,
                        cache_path=cache_path,
                        outcome="invalid",
                        error_message=reason,
                    )
                )
                continue

            attempts.append(
                MetadataCacheAttempt(
                    cache_dir=cache_dir,
                    cache_path=cache_path,
                    outcome="hit",
                )
            )
            self.cache_dir = cache_dir
            self.cache_path = cache_path
            return MetadataCacheLoadResult(
                status="hit" if idx == 0 else "fallback_hit",
                snapshot=snapshot,
                cache_dir=cache_dir,
                cache_path=cache_path,
                attempts=tuple(attempts),
            )

        return MetadataCacheLoadResult(
            status="fallback_fail" if saw_failure else "miss",
            snapshot=None,
            cache_dir=None,
            cache_path=None,
            attempts=tuple(attempts),
        )

    def persist(
        self,
        cache_key: str,
        manifest_hash: str,
        manifest: dict[tuple[str, str], tuple[int, int]],
        sessions: list[SessionRecord],
    ) -> MetadataCachePersistResult:
        if not self.enabled:
            return MetadataCachePersistResult(
                status="miss",
                cache_dir=None,
                cache_path=None,
            )

        payload = {
            "version": METADATA_CACHE_VERSION,
            "schema_version": METADATA_SCHEMA_VERSION,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "cache_key": cache_key,
            "manifest_hash": manifest_hash,
            "manifest": [
                {
                    "provider": provider,
                    "source_path": source_path,
                    "mtime_ns": mtime_ns,
                    "size": size,
                }
                for (provider, source_path), (mtime_ns, size) in sorted(manifest.items())
            ],
            "sessions": [serialize_session_record(item) for item in sessions],
        }

        attempts: list[MetadataCacheAttempt] = []
        for idx, cache_dir in enumerate(self._cache_dirs):
            cache_path = cache_dir / "metadata_snapshot.json"
            ok, error = _atomic_write_json_with_error(cache_dir, cache_path, payload)
            if not ok:
                attempts.append(
                    MetadataCacheAttempt(
                        cache_dir=cache_dir,
                        cache_path=cache_path,
                        outcome="error",
                        error_type=type(error).__name__ if error else None,
                        error_message=str(error) if error else None,
                    )
                )
                continue

            attempts.append(
                MetadataCacheAttempt(
                    cache_dir=cache_dir,
                    cache_path=cache_path,
                    outcome="hit",
                )
            )
            self.cache_dir = cache_dir
            self.cache_path = cache_path
            return MetadataCachePersistResult(
                status="hit" if idx == 0 else "fallback_hit",
                cache_dir=cache_dir,
                cache_path=cache_path,
                attempts=tuple(attempts),
            )

        # Metadata cache is best-effort but we avoid repeatedly attempting writes
        # when all candidate cache paths are unavailable.
        self.enabled = False
        return MetadataCachePersistResult(
            status="write_fail",
            cache_dir=None,
            cache_path=None,
            attempts=tuple(attempts),
        )


def serialize_session_record(record: SessionRecord) -> dict[str, Any]:
    return {
        "provider": record.provider,
        "session_id": record.session_id,
        "source_path": str(record.source_path),
        "started_at": _serialize_datetime(record.started_at),
        "updated_at": _serialize_datetime(record.updated_at),
        "working_dir": record.working_dir,
        "model": record.model,
        "messages": [
            {
                "role": message.role,
                "content": message.content,
                "created_at": _serialize_datetime(message.created_at),
            }
            for message in record.messages
        ],
        "normalized_messages": [
            {
                "id": msg.id,
                "role": msg.role,
                "name": msg.name,
                "timestamp": _serialize_datetime(msg.timestamp),
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


def deserialize_session_record(payload: dict[str, Any]) -> SessionRecord:
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


def _serialize_datetime(value: datetime | None) -> str | None:
    if not value:
        return None
    return value.isoformat()


def _json_friendly(value: Any) -> Any:
    if value is None:
        return None
    try:
        json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return value
    except TypeError:
        return str(value)


def _load_json_payload(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_json_payload_with_error(path: Path) -> tuple[Any, Exception | None]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, exc
    try:
        return json.loads(raw), None
    except json.JSONDecodeError as exc:
        return None, exc


def _parse_metadata_snapshot(
    payload: Any,
    cache_key: str,
) -> tuple[CachedMetadataSnapshot | None, Literal["hit", "miss", "invalid"], str | None]:
    if not isinstance(payload, dict):
        return None, "invalid", "payload_not_dict"

    version = payload.get("version")
    if version != METADATA_CACHE_VERSION:
        return None, "miss", "version_mismatch"

    schema_version = payload.get("schema_version")
    if schema_version != METADATA_SCHEMA_VERSION:
        return None, "miss", "schema_version_mismatch"

    stored_cache_key = payload.get("cache_key")
    if stored_cache_key != cache_key:
        return None, "miss", "cache_key_mismatch"

    raw_manifest = payload.get("manifest")
    if not isinstance(raw_manifest, list):
        return None, "invalid", "manifest_invalid"
    manifest: dict[tuple[str, str], tuple[int, int]] = {}
    for entry in raw_manifest:
        if not isinstance(entry, dict):
            continue
        provider = entry.get("provider")
        source_path = entry.get("source_path")
        mtime_ns = entry.get("mtime_ns")
        size = entry.get("size")
        if (
            isinstance(provider, str)
            and isinstance(source_path, str)
            and isinstance(mtime_ns, int)
            and isinstance(size, int)
        ):
            manifest[(provider, source_path)] = (mtime_ns, size)

    raw_sessions = payload.get("sessions")
    if not isinstance(raw_sessions, list):
        return None, "invalid", "sessions_invalid"
    sessions = [
        deserialize_session_record(entry) for entry in raw_sessions if isinstance(entry, dict)
    ]

    manifest_hash = payload.get("manifest_hash")
    if not isinstance(manifest_hash, str):
        manifest_hash = ""

    return (
        CachedMetadataSnapshot(
            cache_key=cache_key,
            manifest_hash=manifest_hash,
            manifest=manifest,
            sessions=sessions,
        ),
        "hit",
        None,
    )


def _atomic_write_json(cache_dir: Path, cache_path: Path, payload: dict[str, Any]) -> bool:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp_path.replace(cache_path)
    except OSError:
        return False
    return True


def _atomic_write_json_with_error(
    cache_dir: Path,
    cache_path: Path,
    payload: dict[str, Any],
) -> tuple[bool, Exception | None]:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        tmp_path = cache_path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        tmp_path.replace(cache_path)
    except OSError as exc:
        return False, exc
    return True, None
