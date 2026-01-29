"""Session provider for Google Gemini CLI sessions."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path

from ..model import SessionRecord
from ..normalize import Normalizer
from ..util import coalesce, parse_timestamp, stringify_content
from .base import SessionProvider
from .ingest import SessionBuilder


def _gemini_sort_key(record: SessionRecord) -> float:
    dt = record.updated_at or record.started_at
    return dt.timestamp() if isinstance(dt, datetime) else float("-inf")


def _gemini_candidate_files(base_dir: Path) -> list[Path]:
    seen: set[Path] = set()
    candidates: list[Path] = []
    for root in _gemini_roots(base_dir):
        if not root.exists():
            continue
        tmp_dir = root / "tmp"
        if tmp_dir.exists():
            for pattern in (
                "**/chats/*.json",
                "**/checkpoints/*.json",
                "**/session-*.json",
                "**/chat-*.json",
            ):
                for path in tmp_dir.glob(pattern):
                    if path.is_file() and path not in seen:
                        seen.add(path)
                        candidates.append(path)
        history_dir = root / "history"
        if history_dir.exists():
            for path in history_dir.glob("**/*.json"):
                if path.is_file() and path not in seen:
                    seen.add(path)
                    candidates.append(path)
        for pattern in ("checkpoints/*.json", "checkpoints/**/*.json"):
            for path in root.glob(pattern):
                if path.is_file() and path not in seen:
                    seen.add(path)
                    candidates.append(path)
    return sorted(candidates)


def _gemini_roots(base_dir: Path) -> list[Path]:
    roots = [base_dir]
    config_candidates = [
        Path(os.path.expanduser("~/.config/google-generative-ai")),
        Path(os.path.expanduser("~/.local/share/google-generative-ai")),
        Path.home() / "Library/Application Support/google/generative-ai",
    ]
    appdata = os.getenv("APPDATA")
    if appdata:
        config_candidates.append(Path(appdata) / "google" / "generative-ai")
    for candidate in config_candidates:
        if candidate not in roots:
            roots.append(candidate)
    return roots


def _gemini_session_timestamp(payload: dict, key: str) -> datetime | None:
    return parse_timestamp(payload.get(key)) if isinstance(payload, dict) else None


def _gemini_session_id(payload: dict, path: Path) -> str:
    candidate = coalesce(
        payload.get("sessionId"),
        payload.get("session_id"),
        payload.get("conversationId"),
        payload.get("conversation_id"),
        (
            (payload.get("conversation") or {}).get("id")
            if isinstance(payload.get("conversation"), dict)
            else None
        ),
        payload.get("checkpoint_id"),
    )
    if isinstance(candidate, str) and candidate.strip():
        return candidate.strip()
    session_id = path.stem
    if path.parent.name not in {"checkpoints", "history"}:
        return f"{path.parent.name}:{session_id}"
    try:
        return str(path.relative_to(path.parent.parent))
    except ValueError:
        return session_id


def _gemini_messages(payload: dict, *, normalizer: Normalizer) -> tuple[list, str | None]:
    raw_messages = payload.get("messages") if isinstance(payload, dict) else None
    if not isinstance(raw_messages, list):
        return ([], None)

    seen: set[tuple[str, str, str | None]] = set()
    found_model: str | None = None
    normalized_messages = []

    for entry in raw_messages:
        if not isinstance(entry, dict):
            continue
        role = coalesce(entry.get("role"), entry.get("type"), entry.get("speaker"))

        timestamp = parse_timestamp(
            coalesce(
                entry.get("timestamp"),
                entry.get("create_time"),
                entry.get("created_at"),
                entry.get("time"),
                entry.get("ts"),
            )
        )

        normalized = normalizer.normalize_message(entry, timestamp=timestamp, role=role)
        if not normalized:
            continue

        key = (
            normalized.role,
            stringify_content(
                entry.get("content") if "content" in entry else entry.get("parts")
            ).strip(),
            timestamp.isoformat() if timestamp else None,
        )
        if key in seen:
            continue
        seen.add(key)
        normalized_messages.append(normalized)

        if found_model is None:
            candidate = entry.get("model") or entry.get("metadata", {}).get("model")
            if isinstance(candidate, str) and candidate.strip():
                found_model = candidate.strip()

    if found_model is None and isinstance(payload, dict):
        candidate = payload.get("model")
        if isinstance(candidate, str) and candidate.strip():
            found_model = candidate.strip()

    return normalized_messages, found_model


def _gemini_workdir(payload: dict) -> str | None:
    candidates = []
    if isinstance(payload, dict):
        candidates.extend(
            payload.get(key)
            for key in (
                "cwd",
                "working_directory",
                "workspace_root",
                "project_root",
                "projectPath",
                "workingDir",
                "root",
            )
        )
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            project_meta = (
                metadata.get("project") if isinstance(metadata.get("project"), dict) else metadata
            )
            if isinstance(project_meta, dict):
                candidates.extend(
                    project_meta.get(key) for key in ("cwd", "root", "workspace", "workspace_root")
                )
        project = payload.get("project")
        if isinstance(project, dict):
            candidates.extend(project.get(key) for key in ("cwd", "workspace_root", "root"))
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


class GeminiProvider(SessionProvider):
    name = "gemini-cli"
    env_var = "GEMINI_HOME"
    home_subdir = ".gemini"
    glob_patterns: tuple[str, ...] = ()

    def session_paths(self) -> Iterable[Path]:
        return _gemini_candidate_files(self.base_dir)

    def _build_session_from_path(self, path: Path) -> SessionRecord | None:
        try:
            with path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return None

        builder = SessionBuilder(
            provider=self.name,
            source_path=path,
        )
        builder.set_session_id(_gemini_session_id(payload, path))
        builder.set_working_dir(_gemini_workdir(payload))

        started_at = _gemini_session_timestamp(payload, "startTime")
        updated_at = _gemini_session_timestamp(payload, "lastUpdated")
        builder.record_timestamp(started_at)
        builder.record_timestamp(updated_at)

        normalizer = Normalizer(provider=self.name)
        normalized_messages, model = _gemini_messages(payload, normalizer=normalizer)
        builder.normalization_diagnostics = normalizer.diagnostics
        for message in normalized_messages:
            builder.add_normalized_message(message)

        if model:
            builder.set_model(model, priority=2)

        if (not started_at or not updated_at) and normalized_messages:
            msg_times = [msg.timestamp for msg in normalized_messages if msg.timestamp]
            if msg_times:
                builder.record_timestamp(min(msg_times))
                builder.record_timestamp(max(msg_times))

        return builder.build()

    def sessions(self) -> Iterable[SessionRecord]:
        records: dict[str, SessionRecord] = {}
        for record in self._collect_sessions():
            existing = records.get(record.session_id)
            if existing is None or _gemini_sort_key(record) > _gemini_sort_key(existing):
                records[record.session_id] = record
        return sorted(records.values(), key=_gemini_sort_key, reverse=True)
