from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_sessions.model import NormalizedMessage, NormalizedPart
from agent_sessions.normalize import Normalizer
from agent_sessions.providers.ingest import SessionBuilder


def test_normalize_tool_result_role_overrides_user() -> None:
    ts = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
    normalizer = Normalizer(provider="unit-test")
    payload = {
        "role": "user",
        "content": [{"type": "tool_result", "tool_name": "read_file", "output": {"path": "a.txt"}}],
    }

    message = normalizer.normalize_message(payload, timestamp=ts)

    assert message is not None
    assert message.role == "tool"
    assert message.parts[0].kind == "tool-result"
    assert message.parts[0].tool_name == "read_file"


def test_normalize_mixed_parts_extracts_tool_call_and_result() -> None:
    ts = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
    normalizer = Normalizer(provider="unit-test")
    payload = {
        "role": "assistant",
        "content": [
            {"type": "text", "text": "Working..."},
            {"type": "tool_use", "name": "read_file", "input": {"path": "a.txt"}, "id": "c1"},
            {
                "type": "tool_result",
                "tool_name": "read_file",
                "output": "ok",
                "tool_use_id": "c1",
            },
        ],
    }

    message = normalizer.normalize_message(payload, timestamp=ts)

    assert message is not None
    assert message.role == "tool"
    assert [part.kind for part in message.parts] == ["text", "tool-call", "tool-result"]
    assert message.parts[1].tool_name == "read_file"
    assert message.parts[2].tool_name == "read_file"


def test_missing_timestamps_do_not_break_ordering() -> None:
    builder = SessionBuilder(provider="unit-test", source_path=Path("/tmp/session.jsonl"))
    with_ts = datetime(2026, 1, 10, 12, tzinfo=timezone.utc)
    builder.add_normalized_message(
        NormalizedMessage(
            id="m1",
            role="assistant",
            timestamp=None,
            parts=[NormalizedPart(kind="text", text="no timestamp")],
        )
    )
    builder.add_normalized_message(
        NormalizedMessage(
            id="m2",
            role="assistant",
            timestamp=with_ts,
            parts=[NormalizedPart(kind="text", text="with timestamp")],
        )
    )

    record = builder.build(session_id="s1")
    assert record is not None
    assert [msg.content for msg in record.messages] == ["no timestamp", "with timestamp"]


def test_normalization_diagnostics_counts_skips() -> None:
    normalizer = Normalizer(provider="unit-test")
    assert normalizer.normalize_message({}) is None
    assert normalizer.normalize_message({"role": "user", "content": "hi"}) is not None

    diag = normalizer.diagnostics
    assert diag.total_events == 2
    assert diag.parsed_events == 1
    assert diag.skipped_events == 1
