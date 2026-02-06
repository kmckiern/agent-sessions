"""Tests for HTTP static file serving."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from io import BytesIO
from pathlib import Path
from typing import cast
from urllib.parse import quote, urlparse

import agent_sessions.server as server_module
from agent_sessions.data_store import SessionService
from agent_sessions.model import Message, SessionRecord
from agent_sessions.providers.base import SessionProvider
from agent_sessions.server import SessionApi, SessionRouter, serve_static_file


class DummyHandler:
    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: dict[str, str] = {}
        self.error: tuple[int, str | None] | None = None
        self.wfile = BytesIO()

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, key: str, value: str) -> None:
        self.headers[key] = value

    def end_headers(self) -> None:
        return None

    def send_error(self, status: int, message: str | None = None) -> None:
        self.error = (status, message)


class StubProvider(SessionProvider):
    name = "stub"

    def __init__(self, records: list[SessionRecord]) -> None:
        self._records = records
        super().__init__(base_dir=Path("/tmp"))

    @classmethod
    def default_base_dir(cls) -> Path:
        return Path("/tmp")

    def sessions(self):
        return list(self._records)


def make_record(session_id: str, *, provider: str, model: str | None) -> SessionRecord:
    timestamp = datetime(2025, 10, 7, 15, tzinfo=timezone.utc)
    return SessionRecord(
        provider=provider,
        session_id=session_id,
        source_path=Path(f"/tmp/{session_id}.jsonl"),
        started_at=timestamp,
        updated_at=timestamp,
        working_dir="/work",
        model=model,
        messages=[Message(role="assistant", content="ok", created_at=timestamp)],
    )


def dispatch_with_404(router: SessionRouter, handler: DummyHandler, path: str) -> bool:
    parsed = urlparse(path)
    handled = router.dispatch(cast(BaseHTTPRequestHandler, handler), parsed)
    if not handled:
        handler.send_error(HTTPStatus.NOT_FOUND, "Not Found")
    return handled


def test_static_rejects_path_traversal(tmp_path) -> None:
    static_root = tmp_path / "static"
    static_root.mkdir()
    (static_root / "index.html").write_text("ok", encoding="utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")

    handler = DummyHandler()
    assert not serve_static_file(
        cast(BaseHTTPRequestHandler, handler),
        static_root,
        "../outside.txt",
    )
    assert handler.status is None
    assert handler.wfile.getvalue() == b""

    encoded = DummyHandler()
    assert not serve_static_file(
        cast(BaseHTTPRequestHandler, encoded),
        static_root,
        "%2e%2e%2foutside.txt",
    )
    assert encoded.status is None
    assert encoded.wfile.getvalue() == b""


def test_static_missing_file_returns_404(tmp_path) -> None:
    static_root = tmp_path / "static"
    static_root.mkdir()
    service = SessionService(providers=[], refresh_interval=None)
    router = SessionRouter(api=SessionApi(service), static_root=static_root)
    handler = DummyHandler()

    handled = dispatch_with_404(router, handler, "/static/missing.js")

    assert not handled
    assert handler.error == (HTTPStatus.NOT_FOUND, "Not Found")


def test_models_endpoint_aggregates_and_sorts() -> None:
    records = [
        make_record("s1", provider="openai-codex", model="gpt-5-codex"),
        make_record("s2", provider="openai-codex", model="gpt-5-codex"),
        make_record("s3", provider="claude-code", model="claude-sonnet"),
        make_record("s4", provider="claude-code", model=None),
    ]
    service = SessionService(providers=[StubProvider(records)], refresh_interval=None)
    api = SessionApi(service)
    handler = DummyHandler()

    assert api.dispatch(cast(BaseHTTPRequestHandler, handler), "/api/models", "")
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))

    assert [item["id"] for item in payload["models"]] == ["gpt-5-codex", "claude-sonnet"]
    assert payload["models"][0]["count"] == 2
    assert payload["models"][0]["providers"] == ["openai-codex"]


def test_search_hits_endpoint_returns_snippets() -> None:
    ts_new = datetime(2025, 10, 7, 16, tzinfo=timezone.utc)
    ts_old = datetime(2025, 10, 7, 14, tzinfo=timezone.utc)
    records = [
        SessionRecord(
            provider="openai-codex",
            session_id="s1",
            source_path=Path("/tmp/s1.jsonl"),
            started_at=ts_new,
            updated_at=ts_new,
            working_dir="/work",
            model="gpt-5-codex",
            messages=[Message(role="assistant", content="Hello world", created_at=ts_new)],
        ),
        SessionRecord(
            provider="openai-codex",
            session_id="s2",
            source_path=Path("/tmp/s2.jsonl"),
            started_at=ts_old,
            updated_at=ts_old,
            working_dir="/work",
            model="gpt-5-codex",
            messages=[Message(role="assistant", content="No match here", created_at=ts_old)],
        ),
    ]
    service = SessionService(providers=[StubProvider(records)], refresh_interval=None)
    api = SessionApi(service)
    handler = DummyHandler()

    assert api.dispatch(cast(BaseHTTPRequestHandler, handler), "/api/search-hits", "search=hello")
    payload = json.loads(handler.wfile.getvalue().decode("utf-8"))

    assert payload["hits"]
    hit = payload["hits"][0]
    assert hit["session_id"] == "s1"
    assert hit["match_start"] == 0
    assert hit["match_length"] == 5
    assert "hello" in hit["snippet"].lower()


def test_session_detail_endpoint_uses_cached_payload_on_second_open(tmp_path, monkeypatch) -> None:
    source = tmp_path / "s1.jsonl"
    source.write_text('{"event":"x"}\n', encoding="utf-8")
    record = SessionRecord(
        provider="direct",
        session_id="s1",
        source_path=source,
        started_at=datetime(2025, 10, 7, 15, tzinfo=timezone.utc),
        updated_at=datetime(2025, 10, 7, 15, tzinfo=timezone.utc),
        working_dir="/work",
        model="gpt-5-codex",
        messages=[Message(role="assistant", content="Hello world", created_at=None)],
    )

    class DirectProvider(StubProvider):
        name = "direct"

        def __init__(self, records: list[SessionRecord]) -> None:
            self.direct_calls = 0
            super().__init__(records)

        def load_session_from_source_path(
            self,
            source_path: str,
            session_id: str | None,
        ) -> SessionRecord | None:
            self.direct_calls += 1
            if source_path != str(record.source_path):
                return None
            if session_id and session_id != record.session_id:
                return None
            return record

    call_count = {"count": 0}
    original = server_module.session_detail

    def _counting_detail(session: SessionRecord) -> dict[str, object]:
        call_count["count"] += 1
        return original(session)

    monkeypatch.setattr(server_module, "session_detail", _counting_detail)

    provider = DirectProvider([record])
    service = SessionService(providers=[provider], refresh_interval=None)
    api = SessionApi(service)

    path = f"/api/sessions/{record.provider}/{record.session_id}"
    query = f"source_path={quote(str(record.source_path), safe='')}"

    first = DummyHandler()
    second = DummyHandler()
    assert api.dispatch(cast(BaseHTTPRequestHandler, first), path, query)
    assert api.dispatch(cast(BaseHTTPRequestHandler, second), path, query)
    assert call_count["count"] == 1
