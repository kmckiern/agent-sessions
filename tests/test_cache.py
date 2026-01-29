from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_sessions.cache import DiskSessionCache
from agent_sessions.model import Message, SessionRecord


def _write_dummy(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def test_disk_cache_round_trip(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    _write_dummy(session_path, '{"type":"message","content":"hi"}\n')

    record = SessionRecord(
        provider="openai-codex",
        session_id="abc123",
        source_path=session_path,
        started_at=datetime(2026, 1, 13, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 13, 1, 2, 3, tzinfo=timezone.utc),
        working_dir="/tmp",
        model="gpt-test",
        messages=[Message(role="user", content="hello", created_at=None)],
    )

    cache = DiskSessionCache(tmp_path, enabled=True)
    cache.store(record.provider, session_path, record)
    cache.persist()

    fresh_cache = DiskSessionCache(tmp_path, enabled=True)
    fresh_cache.load()
    cached = fresh_cache.lookup(record.provider, session_path)

    assert cached is not None
    assert cached.session_id == "abc123"
    assert cached.provider == "openai-codex"
    assert cached.working_dir == "/tmp"
    assert cached.messages[0].content == "hello"


def test_disk_cache_miss_on_change(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    _write_dummy(session_path, '{"type":"message","content":"hi"}\n')

    record = SessionRecord(
        provider="openai-codex",
        session_id="abc123",
        source_path=session_path,
        started_at=None,
        updated_at=None,
        working_dir=None,
        model=None,
        messages=[Message(role="user", content="hello", created_at=None)],
    )

    cache = DiskSessionCache(tmp_path, enabled=True)
    cache.store(record.provider, session_path, record)
    cache.persist()

    _write_dummy(session_path, '{"type":"message","content":"changed"}\n')

    fresh_cache = DiskSessionCache(tmp_path, enabled=True)
    fresh_cache.load()
    cached = fresh_cache.lookup(record.provider, session_path)

    assert cached is None


def test_disk_cache_persist_is_best_effort(tmp_path: Path) -> None:
    session_path = tmp_path / "session.jsonl"
    _write_dummy(session_path, '{"type":"message","content":"hi"}\n')

    record = SessionRecord(
        provider="openai-codex",
        session_id="abc123",
        source_path=session_path,
        started_at=None,
        updated_at=None,
        working_dir=None,
        model=None,
        messages=[Message(role="user", content="hello", created_at=None)],
    )

    # Make the cache directory invalid by creating a file in its place.
    not_a_dir = tmp_path / "not-a-dir"
    not_a_dir.write_text("nope", encoding="utf-8")

    cache = DiskSessionCache(not_a_dir, enabled=True)
    cache.store(record.provider, session_path, record)
    cache.persist()

    assert cache.enabled is False
