"""Session provider for Anthropic Claude Code CLI."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..model import Message, SessionRecord
from ..normalize import Normalizer
from ..util import coalesce, parse_timestamp, stringify_content
from .base import SessionProvider
from .ingest import SessionBuilder, merge_session_records
from .logging import debug_warning

_UUID_PATTERN = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


@dataclass
class ConversationMeta:
    project_id: str | None = None
    working_dir: str | None = None
    started_at: datetime | None = None
    updated_at: datetime | None = None


def _claude_session_id(path: Path) -> str:
    """Derive a compact, human-friendly session id from a log path."""
    stem = path.stem
    if _UUID_PATTERN.match(stem):
        return stem

    stem_parts = [part for part in stem.split("-") if part]
    if len(stem_parts) >= 5:
        return "-".join(stem_parts[-5:])

    if len(stem) >= 8:
        return stem

    parent = path.parent.name
    return f"{parent}:{stem}" if parent else stem


def _project_workdir(project_dir: Path) -> str | None:
    metadata_files = (
        "project.json",
        "metadata.json",
        "project_metadata.json",
        "manifest.json",
    )
    for name in metadata_files:
        candidate = project_dir / name
        if not candidate.exists():
            continue
        try:
            with candidate.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            debug_warning(f"Skipping unreadable project metadata {candidate}", exc)
            continue
        for key in ("absolutePath", "projectPath", "workspaceRoot", "rootPath", "path"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value
        for container in ("project", "workspace", "meta"):
            nested = payload.get(container)
            if isinstance(nested, dict):
                for key in (
                    "absolutePath",
                    "projectPath",
                    "workspaceRoot",
                    "rootPath",
                    "path",
                ):
                    value = nested.get(key)
                    if isinstance(value, str) and value.strip():
                        return value
    return None


def _claude_event_timestamp(event: dict) -> datetime | None:
    payload = event.get("message") if isinstance(event.get("message"), dict) else {}
    return parse_timestamp(
        coalesce(
            event.get("timestamp"),
            event.get("created_at"),
            event.get("time"),
            event.get("ts"),
            payload.get("timestamp") if isinstance(payload, dict) else None,
            payload.get("createdAt") if isinstance(payload, dict) else None,
        )
    )


def _claude_event_workdir(event: dict) -> str | None:
    candidates = [
        event.get("cwd"),
        event.get("workspace_root"),
        event.get("project_path"),
    ]
    for key in ("workspace", "project", "session", "context"):
        nested = event.get(key)
        if isinstance(nested, dict):
            candidates.extend(
                nested.get(field)
                for field in ("cwd", "workspace_root", "project_path", "root", "path")
            )
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate
    return None


def _claude_event_message(event: dict) -> tuple[str | None, str] | None:
    payload = event.get("message")
    if not isinstance(payload, dict):
        return None

    content = stringify_content(payload.get("content"))
    text = content.strip()
    if not text:
        return None

    role = payload.get("role") or event.get("type") or payload.get("type")
    return role, text


def _merge_session(records: dict[str, SessionRecord], record: SessionRecord) -> None:
    existing = records.get(record.session_id)
    if existing is None:
        records[record.session_id] = record
        return

    def _message_key(message: Message) -> tuple[str, str, str | None]:
        timestamp = message.created_at.isoformat() if message.created_at else None
        return (message.role, message.content, timestamp)

    merged: dict[tuple[str, str, str | None], Message] = {}
    for message in (*existing.messages, *record.messages):
        if not message.content:
            continue
        merged[_message_key(message)] = message

    existing.messages = sorted(
        merged.values(),
        key=lambda msg: msg.created_at.timestamp() if msg.created_at else float("-inf"),
    )
    timestamps = [ts for ts in (existing.started_at, record.started_at) if ts is not None]
    existing.started_at = (
        min(timestamps) if timestamps else existing.started_at or record.started_at
    )
    timestamps = [ts for ts in (existing.updated_at, record.updated_at) if ts is not None]
    existing.updated_at = (
        max(timestamps) if timestamps else existing.updated_at or record.updated_at
    )
    if existing.working_dir is None:
        existing.working_dir = record.working_dir


def _ingest_store_logs(db_path: Path, records: dict[str, SessionRecord]) -> None:
    if not db_path.exists():
        return
    sessions = _load_store_sessions(db_path)
    for record in sessions:
        _merge_session(records, record)


def _load_store_sessions(db_path: Path) -> list[SessionRecord]:
    try:
        connection = sqlite3.connect(db_path)
        connection.row_factory = sqlite3.Row
    except sqlite3.Error as exc:
        debug_warning(f"Unable to open Claude store database {db_path}", exc)
        return []

    try:
        project_paths = _collect_project_paths(connection)
        meta = _collect_conversation_meta(connection)
        messages = _collect_conversation_messages(connection)
    finally:
        connection.close()

    sessions: list[SessionRecord] = []
    for conversation_id, message_list in messages.items():
        if not message_list:
            continue

        normalizer = Normalizer(provider="claude-code")
        normalized_messages = []
        for msg in message_list:
            normalized = normalizer.normalize_message(
                {"role": msg.role, "content": msg.content},
                timestamp=msg.created_at,
                role=msg.role,
            )
            if normalized:
                normalized_messages.append(normalized)

        metadata = meta.get(conversation_id, ConversationMeta())
        started_at = min((msg.created_at for msg in message_list if msg.created_at), default=None)
        updated_at = max((msg.created_at for msg in message_list if msg.created_at), default=None)

        working_dir = metadata.working_dir
        project_id = metadata.project_id
        if working_dir is None and project_id:
            working_dir = project_paths.get(project_id)

        metadata_started = metadata.started_at or started_at
        metadata_updated = metadata.updated_at or updated_at

        sessions.append(
            SessionRecord(
                provider="claude-code",
                session_id=f"store:{conversation_id}",
                source_path=db_path,
                started_at=metadata_started,
                updated_at=metadata_updated,
                working_dir=working_dir,
                messages=sorted(
                    message_list,
                    key=lambda msg: (
                        msg.created_at.timestamp() if msg.created_at else float("-inf")
                    ),
                ),
                normalized_messages=sorted(
                    normalized_messages,
                    key=lambda msg: (msg.timestamp.timestamp() if msg.timestamp else float("-inf")),
                ),
                normalization_diagnostics=normalizer.diagnostics,
            )
        )
    return sessions


def _collect_project_paths(connection: sqlite3.Connection) -> dict[str, str]:
    """
    Map project identifiers to filesystem paths.

    Claude store schemas vary between versions, so we probe known columns and
    tolerate missing tables instead of assuming a fixed layout.
    """
    paths: dict[str, str] = {}
    for table in ("projects", "project_metadata"):
        if not _table_exists(connection, table):
            continue
        columns = _table_columns(connection, table)
        id_column = _first_key(columns, ("id", "project_id", "uuid"))
        path_column = _first_key(
            columns,
            ("absolute_path", "project_path", "workspace_root", "root_path", "path"),
        )
        if not id_column or not path_column:
            continue
        try:
            cursor = connection.execute(f"SELECT {id_column}, {path_column} FROM {table}")
        except sqlite3.Error as exc:
            debug_warning(f"Failed to read project paths from {table}", exc)
            continue
        for row in cursor:
            identifier = row[id_column]
            raw_path = row[path_column]
            if isinstance(identifier, str | int) and isinstance(raw_path, str) and raw_path.strip():
                paths[str(identifier)] = raw_path
    return paths


def _collect_conversation_meta(
    connection: sqlite3.Connection,
) -> dict[str, ConversationMeta]:
    """
    Extract working directory metadata and timestamps for each conversation.

    Different builds of the Claude CLI rename columns, so we pick the first
    matching candidate for identifiers, project references, and timestamps and
    gracefully skip tables that cannot be queried.
    """
    meta: dict[str, ConversationMeta] = {}
    for table in ("conversations", "conversation_summaries"):
        _ingest_conversation_meta_table(connection, table, meta)
    return meta


def _collect_conversation_messages(
    connection: sqlite3.Connection,
) -> dict[str, list[Message]]:
    """
    Gather message content for each conversation across multiple possible tables.

    Table and column names differ across releases. We sniff for conversation id,
    role, content, and timestamp columns and normalise message records into
    Message objects, skipping rows with no usable content.
    """
    conversations: dict[str, list[Message]] = defaultdict(list)
    message_tables = [
        ("conversation_messages", None),
        ("messages", None),
        ("base_messages", None),
        ("assistant_messages", "assistant"),
        ("user_messages", "user"),
    ]
    for table, default_role in message_tables:
        _ingest_message_table(connection, table, default_role, conversations)
    return conversations


def _ingest_conversation_meta_table(
    connection: sqlite3.Connection, table: str, meta: dict[str, ConversationMeta]
) -> None:
    if not _table_exists(connection, table):
        return
    columns = _table_columns(connection, table)
    id_column = _first_key(columns, ("conversation_id", "conversation_uuid", "id", "uuid"))
    if not id_column:
        return

    project_column = _first_key(columns, ("project_id", "workspace_id"))
    working_dir_columns = [
        key
        for key in (
            "project_path",
            "workspace_root",
            "root_path",
            "path",
            "absolute_path",
        )
        if key in columns
    ]
    timestamp_columns = [
        key
        for key in ("created_at", "started_at", "updated_at", "last_activity_at")
        if key in columns
    ]
    try:
        cursor = connection.execute(f"SELECT * FROM {table}")
    except sqlite3.Error as exc:
        debug_warning(f"Failed to read conversation metadata from {table}", exc)
        return

    for row in cursor:
        _merge_conversation_meta(
            meta,
            row,
            columns,
            id_column,
            project_column,
            working_dir_columns,
            timestamp_columns,
        )


def _ingest_message_table(
    connection: sqlite3.Connection,
    table: str,
    default_role: str | None,
    conversations: dict[str, list[Message]],
) -> None:
    if not _table_exists(connection, table):
        return
    columns = _table_columns(connection, table)
    conversation_column = _first_key(
        columns,
        ("conversation_id", "conversation_uuid", "conversation", "session_id", "session_uuid"),
    )
    if not conversation_column:
        return

    role_columns = [key for key in ("role", "author", "speaker", "sender") if key in columns]
    content_columns = [
        key
        for key in ("content", "text", "body", "message", "message_json", "payload")
        if key in columns
    ]
    timestamp_column = _first_key(columns, ("created_at", "timestamp", "time", "ts"))

    try:
        cursor = connection.execute(f"SELECT * FROM {table}")
    except sqlite3.Error as exc:
        debug_warning(f"Failed to read conversation messages from {table}", exc)
        return

    for row in cursor:
        conversation_id, message = _extract_message_row(
            row,
            default_role,
            conversation_column,
            role_columns,
            content_columns,
            timestamp_column,
        )
        if conversation_id and message:
            conversations[conversation_id].append(message)


def _merge_conversation_meta(
    meta: dict[str, ConversationMeta],
    row: sqlite3.Row,
    columns: set[str],
    id_column: str,
    project_column: str | None,
    working_dir_columns: list[str],
    timestamp_columns: list[str],
) -> None:
    conversation_id = row[id_column]
    if conversation_id is None:
        return
    conv_key = str(conversation_id)
    entry = meta.setdefault(conv_key, ConversationMeta())

    if project_column and entry.project_id is None:
        proj_value = row[project_column]
        if proj_value is not None:
            entry.project_id = str(proj_value)

    if entry.working_dir is None:
        for key in working_dir_columns:
            value = row[key]
            if isinstance(value, str) and value.strip():
                entry.working_dir = value
                break

    if entry.working_dir is None:
        for key in ("metadata", "project", "workspace", "data"):
            if key not in columns:
                continue
            value = row[key]
            nested = _maybe_json(value) if isinstance(value, str) else value
            if isinstance(nested, dict):
                candidate = _claude_event_workdir(nested)
                if candidate:
                    entry.working_dir = candidate
                    break

    for key in timestamp_columns:
        parsed = parse_timestamp(row[key])
        if parsed is None:
            continue
        if entry.started_at is None or parsed < entry.started_at:
            entry.started_at = parsed
        if entry.updated_at is None or parsed > entry.updated_at:
            entry.updated_at = parsed


def _extract_message_row(
    row: sqlite3.Row,
    default_role: str | None,
    conversation_column: str,
    role_columns: list[str],
    content_columns: list[str],
    timestamp_column: str | None,
) -> tuple[str | None, Message | None]:
    conversation_id = row[conversation_column]
    if conversation_id is None:
        return None, None

    role_value = None
    for key in role_columns:
        value = row[key]
        if isinstance(value, str) and value.strip():
            role_value = value
            break
    if role_value is None:
        role_value = default_role or "event"

    content_value = None
    for key in content_columns:
        value = row[key]
        if value is None:
            continue
        if isinstance(value, str):
            maybe = _maybe_json(value)
            content_value = maybe if maybe is not None else value
        else:
            content_value = value
        if content_value is not None:
            break

    text = stringify_content(content_value).strip()
    if not text and not role_value:
        return None, None

    timestamp = parse_timestamp(row[timestamp_column]) if timestamp_column else None
    message = Message(role=role_value or "event", content=text, created_at=timestamp)
    return str(conversation_id), message


def _maybe_json(value: str):
    stripped = value.strip()
    if not stripped or stripped[0] not in ("{", "["):
        return None
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        return None


def _table_exists(connection: sqlite3.Connection, table: str) -> bool:
    try:
        cursor = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
            (table,),
        )
        return cursor.fetchone() is not None
    except sqlite3.Error:
        return False


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    try:
        cursor = connection.execute(f"PRAGMA table_info('{table}')")
    except sqlite3.Error:
        return set()
    return {row[1] for row in cursor}


def _first_key(columns: set[str], candidates: Iterable[str]) -> str | None:
    for key in candidates:
        if key in columns:
            return key
    return None


class ClaudeProvider(SessionProvider):
    name = "claude-code"
    env_var = "CLAUDE_HOME"
    home_subdir = ".claude"
    glob_patterns = ("projects/*/**/*.jsonl",)

    def __init__(self, base_dir: Path | None = None) -> None:
        super().__init__(base_dir=base_dir)
        self._workdir_cache: dict[Path, str | None] = {}

    def session_id_from_path(self, path: Path) -> str:
        return _claude_session_id(path)

    def create_builder(self, path: Path) -> SessionBuilder:
        builder = super().create_builder(path)
        builder.set_working_dir(self._project_workdir_for(path))
        return builder

    def handle_event(self, builder: SessionBuilder, event: dict) -> None:
        timestamp = _claude_event_timestamp(event)
        builder.record_timestamp(timestamp)

        if not builder.working_dir:
            builder.set_working_dir(_claude_event_workdir(event))

        payload = event.get("message") if isinstance(event.get("message"), dict) else {}
        if isinstance(payload, dict):
            candidate_model = payload.get("model")
            if isinstance(candidate_model, str) and candidate_model.strip():
                priority = 2 if payload.get("role") == "assistant" else 1
                builder.set_model(candidate_model, priority=priority)

        if isinstance(payload, dict):
            normalizer: Normalizer = getattr(builder, "_normalizer", None)  # type: ignore[assignment]
            if normalizer is None:
                normalizer = Normalizer(provider=self.name)
                builder._normalizer = normalizer
            normalized = normalizer.normalize_message(payload, timestamp=timestamp)
            builder.normalization_diagnostics = normalizer.diagnostics
            if normalized:
                builder.add_normalized_message(normalized)

    def sessions(self) -> Iterable[SessionRecord]:
        records: dict[str, SessionRecord] = {}
        for record in self._collect_sessions():
            records[record.session_id] = record

        for record in self.extra_sessions():
            existing = records.get(record.session_id)
            if existing:
                merged = merge_session_records(existing, record)
                records[record.session_id] = merged
            else:
                records[record.session_id] = record

        return self._sorted(records.values())

    def extra_sessions(self) -> Iterable[SessionRecord]:
        return _load_store_sessions(self.base_dir / "__store.db")

    def _project_workdir_for(self, path: Path) -> str | None:
        try:
            relative = path.relative_to(self.base_dir / "projects")
        except ValueError:
            return None
        if not relative.parts:
            return None
        project_dir = self.base_dir / "projects" / relative.parts[0]
        if project_dir in self._workdir_cache:
            return self._workdir_cache[project_dir]
        workdir = _project_workdir(project_dir)
        self._workdir_cache[project_dir] = workdir
        return workdir
