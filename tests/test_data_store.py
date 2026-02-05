"""Tests for the session service cache and pagination."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from agent_sessions.data_store import SessionService, _CacheState
from agent_sessions.model import Message, SessionRecord
from agent_sessions.providers.base import SessionProvider
from agent_sessions.query import SessionQuery


def make_record(
    session_id: str,
    minutes: int,
    *,
    working_dir: str | None = "/workspace",
    model: str | None = "model",
    provider: str = "stub",
) -> SessionRecord:
    started = datetime(2025, 10, 29, 12, tzinfo=timezone.utc) + timedelta(minutes=minutes)
    updated = started + timedelta(minutes=1)
    return SessionRecord(
        provider=provider,
        session_id=session_id,
        source_path=Path(f"/tmp/{session_id}.jsonl"),
        started_at=started,
        updated_at=updated,
        working_dir=working_dir,
        model=model,
        messages=[
            Message(role="user", content="hi", created_at=started),
            Message(role="assistant", content="hello", created_at=updated),
        ],
    )


class StubProvider(SessionProvider):
    name = "stub"

    def __init__(self, records: list[SessionRecord]) -> None:
        self._records = records
        self.calls = 0
        super().__init__(base_dir=Path("/tmp"))

    @classmethod
    def default_base_dir(cls) -> Path:
        return Path("/tmp")

    def sessions(self):
        self.calls += 1
        return list(self._records)


class DirectLoadProvider(StubProvider):
    name = "direct"

    def __init__(self, records: list[SessionRecord], direct_record: SessionRecord | None) -> None:
        self.direct_calls = 0
        self._direct_record = direct_record
        super().__init__(records)

    def load_session_from_source_path(
        self,
        source_path: str,
        session_id: str | None,
    ) -> SessionRecord | None:
        self.direct_calls += 1
        record = self._direct_record
        if record is None or str(record.source_path) != source_path:
            return None
        if session_id and record.session_id != session_id:
            return None
        return record


class FakeClock:
    def __init__(self) -> None:
        self._now = 1000.0

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


def test_cache_state_reload_logic() -> None:
    clock = FakeClock()
    state = _CacheState(refresh_interval=5.0)

    assert state.should_reload(False, now=clock.now)

    state.mark_loaded(clock.now())
    clock.advance(3)
    assert not state.should_reload(True, now=clock.now)

    clock.advance(3)
    assert state.should_reload(True, now=clock.now)

    state = _CacheState(refresh_interval=None)
    state.mark_loaded(clock.now())
    assert not state.should_reload(True, now=clock.now)

    state = _CacheState(refresh_interval=0)
    state.mark_loaded(clock.now())
    assert state.should_reload(True, now=clock.now)


def test_session_service_respects_refresh_interval() -> None:
    records = [make_record("s1", 0)]
    provider = StubProvider(records)
    clock = FakeClock()
    service = SessionService(providers=[provider], refresh_interval=10.0, clock=clock.now)

    assert service.all_sessions()
    assert provider.calls == 1

    clock.advance(5)
    service.all_sessions()
    assert provider.calls == 1

    clock.advance(6)
    service.all_sessions()
    assert provider.calls == 2


def test_session_service_list_sessions_paginates_and_sorts() -> None:
    records = [
        make_record("s1", 0),
        make_record("s2", 10),
        make_record("s3", 20),
    ]
    provider = StubProvider(records)
    clock = FakeClock()
    service = SessionService(providers=[provider], refresh_interval=None, clock=clock.now)

    query = SessionQuery(order="updated_at", page=2, page_size=2)
    page = service.list_sessions(query)

    assert page.total == 3
    assert page.total_pages == 2
    assert page.page == 2
    assert page.page_size == 2
    assert [record.session_id for record in page.items] == ["s1"]
    assert page.has_previous
    assert not page.has_next


def test_session_service_filters_working_dirs() -> None:
    records = [
        make_record("s1", 0, working_dir="/workspace/a"),
        make_record("s2", 10, working_dir="/workspace/b"),
        make_record("s3", 20, working_dir=None),
    ]
    provider = StubProvider(records)
    clock = FakeClock()
    service = SessionService(providers=[provider], refresh_interval=None, clock=clock.now)

    include_query = SessionQuery(include_working_dirs={"/workspace/a"})
    include_page = service.list_sessions(include_query)
    assert [record.session_id for record in include_page.items] == ["s1"]

    exclude_query = SessionQuery(exclude_working_dirs={"/workspace/b"})
    exclude_page = service.list_sessions(exclude_query)
    assert [record.session_id for record in exclude_page.items] == ["s3", "s1"]


def test_session_service_filters_models() -> None:
    records = [
        make_record("s1", 0, model="gpt-5-codex", provider="openai-codex"),
        make_record("s2", 10, model="gpt-4o", provider="openai-codex"),
        make_record("s3", 20, model="claude-sonnet", provider="claude-code"),
    ]
    provider = StubProvider(records)
    clock = FakeClock()
    service = SessionService(providers=[provider], refresh_interval=None, clock=clock.now)

    exact_query = SessionQuery(model_exact={"gpt-4o"})
    exact_page = service.list_sessions(exact_query)
    assert [record.session_id for record in exact_page.items] == ["s2"]

    prefix_query = SessionQuery(model_prefixes={"gpt-"})
    prefix_page = service.list_sessions(prefix_query)
    assert [record.session_id for record in prefix_page.items] == ["s2", "s1"]

    provider_query = SessionQuery(model_prefixes={"gpt-"}, model_provider="claude-code")
    provider_page = service.list_sessions(provider_query)
    assert provider_page.items == []


def test_session_service_direct_load_short_circuits_cache() -> None:
    record = make_record("s1", 0)
    provider = DirectLoadProvider([record], direct_record=record)
    service = SessionService(providers=[provider], refresh_interval=None)

    found = service.get_session(
        provider.name,
        record.session_id,
        source_path=str(record.source_path),
    )

    assert found == record
    assert provider.direct_calls == 1
    assert provider.calls == 0


def test_session_service_direct_load_falls_back_to_cache() -> None:
    record = make_record("s1", 0)
    provider = DirectLoadProvider([record], direct_record=None)
    service = SessionService(providers=[provider], refresh_interval=None)

    found = service.get_session(None, None, source_path=str(record.source_path))

    assert found == record
    assert provider.direct_calls == 1
    assert provider.calls == 1
