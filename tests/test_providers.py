"""Provider-level integration smoke tests."""

from __future__ import annotations

import json
from pathlib import Path

from agent_sessions.providers.claude import ClaudeProvider
from agent_sessions.providers.codex import CodexProvider
from agent_sessions.providers.gemini import GeminiProvider
from agent_sessions.providers.ingest import JsonlReader, SessionBuilder


def _write_jsonl(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event))
            handle.write("\n")


def test_jsonl_reader_skips_invalid(tmp_path):
    path = tmp_path / "events.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        handle.write('{"valid": true}\n')
        handle.write("not-json\n")
        handle.write("\n")
        handle.write('{"another": 1}\n')

    events = list(JsonlReader(path))
    assert events == [{"valid": True}, {"another": 1}]


def test_session_builder_deduplicates_messages(tmp_path):
    builder = SessionBuilder(
        provider="test",
        source_path=tmp_path / "session.jsonl",
    )
    builder.add_message("user", "Hello", None)
    builder.add_message("user", "Hello", None)

    record = builder.build(session_id="session-1")
    assert record is not None
    assert record.message_count == 1


def test_codex_provider_parses_messages(tmp_path):
    base = tmp_path
    session_file = base / "sessions/2025/10/07/rollout.jsonl"
    _write_jsonl(
        session_file,
        [
            {
                "timestamp": "2025-10-07T15:00:00Z",
                "type": "session_meta",
                "payload": {"cwd": "/workspace/project", "model": "gpt-5-codex"},
            },
            {
                "timestamp": "2025-10-07T15:01:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "First question"}],
                },
            },
            {
                "timestamp": "2025-10-07T15:02:00Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": "Answer"}],
                },
            },
        ],
    )

    provider = CodexProvider(base)
    sessions = list(provider.sessions())

    assert len(sessions) == 1
    record = sessions[0]
    assert record.working_dir == "/workspace/project"
    assert record.model == "gpt-5-codex"
    assert record.first_message and record.first_message.content == "First question"
    assert record.last_message and record.last_message.content == "Answer"
    assert record.message_count == 2
    assert len(record.normalized_messages) == 2


def test_codex_provider_tool_result_is_not_user(tmp_path):
    fixture = Path(__file__).with_name("fixtures") / "codex_tool_result.jsonl"
    base = tmp_path
    session_file = base / "sessions/2026/01/10/tool-result.jsonl"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(fixture.read_text(encoding="utf-8"), encoding="utf-8")

    provider = CodexProvider(base)
    sessions = list(provider.sessions())

    assert len(sessions) == 1
    record = sessions[0]
    roles = [message.role for message in record.messages]
    assert "tool" in roles
    assert "user" in roles
    assert any(
        msg.role == "tool" and any(part.kind == "tool-result" for part in msg.parts)
        for msg in record.normalized_messages
    )


def test_claude_provider_skips_empty_summary(tmp_path):
    base = tmp_path
    project_dir = base / "projects/-Users-test-project"
    metadata = {"absolutePath": "/Users/test/project"}
    (project_dir / "metadata.json").parent.mkdir(parents=True, exist_ok=True)
    with (project_dir / "metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle)

    session_file = project_dir / "sessions/history.jsonl"
    _write_jsonl(
        session_file,
        [
            {
                "type": "summary",
                "timestamp": "2025-10-07T15:00:00Z",
                "message": {"role": "summary", "content": ""},
            },
            {
                "type": "user",
                "timestamp": "2025-10-07T15:01:00Z",
                "cwd": "/Users/test/project",
                "message": {
                    "role": "user",
                    "content": "Real content",
                    "model": "claude-user",
                },
            },
            {
                "type": "assistant",
                "timestamp": "2025-10-07T15:02:00Z",
                "message": {
                    "role": "assistant",
                    "content": "Reply",
                    "model": "claude-sonnet",
                },
            },
        ],
    )

    provider = ClaudeProvider(base)
    sessions = list(provider.sessions())

    assert len(sessions) == 1
    record = sessions[0]
    assert record.working_dir == "/Users/test/project"
    assert record.first_message and record.first_message.content == "Real content"
    assert record.model == "claude-sonnet"
    assert record.message_count == 2
    assert len(record.normalized_messages) == 2


def test_claude_provider_trims_session_id(tmp_path):
    base = tmp_path
    project_dir = base / "projects/-Users-sample-project"
    metadata = {"absolutePath": "/Users/sample/project"}
    project_dir.mkdir(parents=True, exist_ok=True)
    with (project_dir / "metadata.json").open("w", encoding="utf-8") as handle:
        json.dump(metadata, handle)

    session_id = "bf2108cf-8b24-4ae1-ac80-cdf4ad2bbf38"
    session_file = project_dir / "logs" / f"{session_id}.jsonl"
    _write_jsonl(
        session_file,
        [
            {
                "type": "assistant",
                "timestamp": "2025-10-07T15:02:00Z",
                "message": {"role": "assistant", "content": "Reply"},
            },
        ],
    )

    provider = ClaudeProvider(base)
    sessions = list(provider.sessions())

    assert len(sessions) == 1
    record = sessions[0]
    assert record.session_id == session_id


def test_gemini_provider_parses_messages(tmp_path):
    base = tmp_path
    checkpoint = base / "tmp/hash/chats/session.json"
    checkpoint.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "sessionId": "session-123",
        "startTime": "2025-10-07T15:00:00Z",
        "lastUpdated": "2025-10-07T15:05:00Z",
        "metadata": {"project": {"cwd": "/Users/test/project"}},
        "messages": [
            {"type": "user", "timestamp": "2025-10-07T15:01:00Z", "content": "Hello"},
            {
                "type": "gemini",
                "timestamp": "2025-10-07T15:02:00Z",
                "content": "Hi there",
                "model": "gemini-unit-test",
            },
        ],
    }
    with checkpoint.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle)

    provider = GeminiProvider(base)
    sessions = list(provider.sessions())

    assert len(sessions) == 1
    record = sessions[0]
    assert record.session_id == "session-123"
    assert record.working_dir == "/Users/test/project"
    assert record.first_message and record.first_message.content == "Hello"
    assert record.last_message and record.last_message.content == "Hi there"
    assert record.message_count == 2
    assert record.model == "gemini-unit-test"
    assert len(record.normalized_messages) == 2
