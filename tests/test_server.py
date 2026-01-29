"""Tests for HTTP static file serving."""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from io import BytesIO
from typing import cast
from urllib.parse import urlparse

from agent_sessions.data_store import SessionService
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
