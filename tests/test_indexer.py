"""Tests for session aggregation helpers."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_sessions.indexer import ProviderConfig, build_providers, load_sessions
from agent_sessions.model import Message, SessionRecord
from agent_sessions.providers.base import SessionProvider


class FakeProvider(SessionProvider):
    name = "fake"
    default_base = Path("/tmp/fake")

    def __init__(
        self, base_dir: Path | None = None, records: list[SessionRecord] | None = None
    ) -> None:
        self._records = records or []
        super().__init__(base_dir)

    @classmethod
    def default_base_dir(cls) -> Path:
        return cls.default_base

    def sessions(self) -> list[SessionRecord]:
        return list(self._records)


class ExplodingProvider(SessionProvider):
    name = "boom"

    @classmethod
    def default_base_dir(cls) -> Path:
        return Path("/tmp/error")

    def sessions(self):
        raise RuntimeError("failure")


def make_record(
    session_id: str, *, updated: datetime | None, started: datetime | None
) -> SessionRecord:
    return SessionRecord(
        provider="fake",
        session_id=session_id,
        source_path=Path(f"/tmp/{session_id}.jsonl"),
        started_at=started,
        updated_at=updated,
        working_dir="/work",
        messages=[
            Message(role="user", content="hi", created_at=started),
            Message(role="assistant", content="hello", created_at=updated or started),
        ],
    )


def test_build_providers_uses_custom_config_base_dir() -> None:
    config = ProviderConfig(provider_cls=FakeProvider, base_dir=Path("/custom"))
    providers = build_providers([config])
    assert len(providers) == 1
    assert isinstance(providers[0], FakeProvider)
    assert providers[0].base_dir == Path("/custom")


def test_load_sessions_sorts_by_updated_timestamp_descending() -> None:
    newer = make_record(
        "session-new",
        updated=datetime(2024, 6, 2, 10, tzinfo=timezone.utc),
        started=datetime(2024, 6, 2, 9, tzinfo=timezone.utc),
    )
    older = make_record(
        "session-old",
        updated=datetime(2024, 6, 1, 10, tzinfo=timezone.utc),
        started=datetime(2024, 6, 1, 9, tzinfo=timezone.utc),
    )
    provider = FakeProvider(records=[older, newer])
    result = load_sessions([provider])
    assert [record.session_id for record in result] == ["session-new", "session-old"]


def test_load_sessions_falls_back_to_started_timestamp() -> None:
    with_updated = make_record(
        "with-updated",
        updated=datetime(2024, 5, 10, 15, tzinfo=timezone.utc),
        started=datetime(2024, 5, 10, 14, tzinfo=timezone.utc),
    )
    without_updated = make_record(
        "without-updated",
        updated=None,
        started=datetime(2024, 5, 11, 9, tzinfo=timezone.utc),
    )
    provider = FakeProvider(records=[with_updated, without_updated])
    result = load_sessions([provider])
    assert [record.session_id for record in result] == [
        "without-updated",
        "with-updated",
    ]


def test_load_sessions_ignores_provider_failures() -> None:
    provider = FakeProvider(
        records=[
            make_record(
                "ok",
                updated=datetime(2024, 5, 12, 12, tzinfo=timezone.utc),
                started=datetime(2024, 5, 12, 11, tzinfo=timezone.utc),
            )
        ]
    )
    result = load_sessions([provider, ExplodingProvider()])
    assert [record.session_id for record in result] == ["ok"]
