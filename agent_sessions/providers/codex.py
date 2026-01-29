"""Codex provider helpers and session loader."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from ..model import SessionRecord
from ..normalize import Normalizer
from ..util import coalesce, parse_timestamp, stringify_content
from .base import SessionProvider


def _codex_timestamp(event: dict) -> datetime | None:
    return parse_timestamp(
        coalesce(
            event.get("timestamp"),
            event.get("created_at"),
            event.get("time"),
            event.get("ts"),
            event.get("stored_at"),
        )
    )


def _flatten_codex_content(payload: dict) -> str:
    content = payload.get("content")
    if content is None and payload.get("summary"):
        content = payload.get("summary")
    return stringify_content(content).strip()


def _codex_workdir(event: dict) -> str | None:
    sources = [event]
    payload = event.get("payload")
    if isinstance(payload, dict):
        sources.append(payload)

    for source in sources:
        candidates = [
            source.get("cwd"),
            source.get("workspace_root"),
            source.get("project_root"),
            source.get("working_directory"),
            source.get("root"),
            source.get("workspace"),
        ]
        for key in ("command", "shell", "run", "workspace"):
            nested = source.get(key)
            if isinstance(nested, dict):
                candidates.extend(
                    nested.get(field) for field in ("cwd", "root", "workspace_root", "project_root")
                )
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate
    return None


def _codex_model(event: dict) -> tuple[str | None, int]:
    payload = event.get("payload")
    if isinstance(payload, dict):
        role = payload.get("role")
        model = payload.get("model")
        if isinstance(model, str) and model.strip():
            priority = 2 if role == "assistant" else 1
            return model, priority
        context = payload.get("context")
        if isinstance(context, dict):
            model = context.get("model")
            if isinstance(model, str) and model.strip():
                return model, 1
    model = event.get("model")
    if isinstance(model, str) and model.strip():
        return model, 0
    return None, -1


def _codex_message_parts(event: dict) -> tuple[str | None, str | None] | None:
    payload = event.get("payload")
    if not isinstance(payload, dict):
        return None
    payload_type = payload.get("type")
    if payload_type == "message":
        text = _flatten_codex_content(payload)
        role = payload.get("role") or event.get("role")
        if not text and not role:
            return None
        return role, text
    return None


def _should_normalize_codex_payload(payload: dict) -> bool:
    payload_type = (payload.get("type") or "").strip().lower()
    if payload_type in {
        "message",
        "tool_result",
        "tool-result",
        "tool_output",
        "tool-output",
        "tool_call",
        "tool-call",
        "tool_use",
        "tool-use",
    }:
        return True
    for key in ("content", "parts", "tool_calls", "function_call"):
        if key in payload:
            return True
    return False


class CodexProvider(SessionProvider):
    name = "openai-codex"
    env_var = "CODEX_HOME"
    home_subdir = ".codex"
    glob_patterns = ("sessions/*/*/*/*.jsonl",)

    def session_id_from_path(self, path: Path) -> str:
        stem_parts = path.stem.split("-")
        if len(stem_parts) >= 5:
            return "-".join(stem_parts[-5:])
        return path.stem

    def load_session_from_source_path(
        self,
        source_path: str,
        session_id: str | None,
    ) -> SessionRecord | None:
        try:
            target = Path(source_path).expanduser()
        except (TypeError, ValueError):
            return None

        try:
            base_dir = self.base_dir.expanduser().resolve(strict=False)
            resolved_target = target.resolve(strict=False)
        except OSError:
            return None

        if not resolved_target.is_file() or not resolved_target.is_relative_to(base_dir):
            return None

        record = self._build_session_from_path_cached(resolved_target)
        if record is None:
            return None
        if session_id and record.session_id != session_id:
            return None
        return record

    def handle_event(self, builder, event: dict) -> None:  # type: ignore[override]
        timestamp = _codex_timestamp(event)
        builder.record_timestamp(timestamp)

        if not builder.working_dir:
            builder.set_working_dir(_codex_workdir(event))

        model, priority = _codex_model(event)
        if model:
            builder.set_model(model, priority=priority)

        payload = event.get("payload")
        if isinstance(payload, dict) and _should_normalize_codex_payload(payload):
            normalizer: Normalizer = getattr(builder, "_normalizer", None)  # type: ignore[assignment]
            if normalizer is None:
                normalizer = Normalizer(provider=self.name)
                builder._normalizer = normalizer
            normalized = normalizer.normalize_message(
                payload,
                timestamp=timestamp,
                role=payload.get("role") or event.get("role"),
            )
            builder.normalization_diagnostics = normalizer.diagnostics
            if normalized:
                builder.add_normalized_message(normalized)
