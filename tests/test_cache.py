from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_sessions.cache import DiskMetadataCache, DiskSessionCache
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


def test_metadata_cache_round_trip(tmp_path: Path) -> None:
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

    manifest = {("openai-codex", str(session_path)): (1234, 56)}
    cache = DiskMetadataCache(tmp_path, enabled=True)
    cache.persist("cache-key", "manifest-hash", manifest, [record])

    restored = cache.load("cache-key")
    assert restored is not None
    assert restored.cache_key == "cache-key"
    assert restored.manifest_hash == "manifest-hash"
    assert restored.manifest == manifest
    assert len(restored.sessions) == 1
    assert restored.sessions[0].session_id == "abc123"


def test_metadata_cache_load_falls_back_on_corruption(tmp_path: Path) -> None:
    cache = DiskMetadataCache(tmp_path, enabled=True)
    cache.cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache.cache_path.write_text("{not-json", encoding="utf-8")

    assert cache.load("cache-key") is None
