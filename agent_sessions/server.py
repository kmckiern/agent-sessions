"""
HTTP server for browsing aggregated session history via JSON API.
"""

from __future__ import annotations

import json
import mimetypes
import os
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TypedDict
from urllib.parse import ParseResult, parse_qs, unquote, urlparse

from .cache import path_fingerprint
from .data_store import SessionService
from .model import SessionRecord
from .providers import get_provider_entry, list_providers
from .query import ORDER_UPDATED_AT, SUPPORTED_ORDERS, SessionQuery, apply_filters, sort_sessions
from .telemetry import log_event
from .util import strip_private_use

STATIC_DIR = Path(__file__).with_name("static")
MAX_PAGE_SIZE = 100
DEFAULT_REFRESH_INTERVAL = float(os.environ.get("AGENT_SESSIONS_REFRESH_INTERVAL", "30"))
DETAIL_CACHE_MAX = 256


class ProviderSummary(TypedDict):
    id: str
    label: str
    env_var: str | None
    default_paths: list[str]
    session_count: int
    last_updated: str | None


class ModelSummary(TypedDict):
    id: str
    label: str
    count: int
    providers: list[str]


def provider_label(name: str) -> str:
    if not name:
        return "Unknown"
    entry = get_provider_entry(name)
    if entry:
        return entry.label
    return name.replace("-", " ").title()


def isoformat_or_none(value) -> str | None:
    return value.isoformat() if value else None


def message_preview(session: SessionRecord) -> str:
    last = session.last_message
    if not last:
        return ""
    preview = strip_private_use(last.content or "").replace("\n", " ").strip()
    return preview[:200]


def _ordered_messages(session: SessionRecord) -> list:
    def sort_key(message) -> float:
        return message.created_at.timestamp() if message.created_at else float("-inf")

    descending = sorted(session.messages, key=sort_key, reverse=True)
    ascending = sorted(descending, key=sort_key)
    return list(reversed(ascending))


def _to_one_line(text: str) -> str:
    normalized = text.replace("\r", " ").replace("\n", " ").replace("\t", " ")
    return " ".join(normalized.split())


def _build_snippet(text: str, match_start: int, match_length: int) -> tuple[str, int, int]:
    if not text:
        return "", 0, 0

    max_len = 220
    total = len(text)
    if total <= max_len:
        return text, match_start, match_length

    context = max(36, (max_len - match_length) // 2)
    start = max(0, match_start - context)
    end = min(total, match_start + match_length + context)
    snippet = text[start:end]
    offset = match_start - start

    if start > 0:
        snippet = "…" + snippet
        offset += 1
    if end < total:
        snippet = snippet + "…"

    return snippet, offset, match_length


def session_summary(session: SessionRecord) -> dict[str, object]:
    return {
        "provider": session.provider,
        "provider_label": provider_label(session.provider),
        "session_id": session.session_id,
        "model": strip_private_use(session.model) if session.model else None,
        "working_dir": (strip_private_use(session.working_dir) if session.working_dir else None),
        "started_at": isoformat_or_none(session.started_at),
        "updated_at": isoformat_or_none(session.updated_at),
        "message_count": session.message_count,
        "preview": message_preview(session),
        "source_path": str(session.source_path),
    }


def session_detail(session: SessionRecord) -> dict[str, object]:
    data = session_summary(session)
    data["messages"] = [
        {
            "role": strip_private_use(message.role),
            "content": strip_private_use(message.content),
            "created_at": isoformat_or_none(message.created_at),
        }
        for message in sorted(
            session.messages,
            key=lambda item: (item.created_at.timestamp() if item.created_at else float("-inf")),
            reverse=True,
        )
    ]
    data["normalized_messages"] = [
        {
            "id": message.id,
            "role": message.role,
            "name": strip_private_use(message.name) if message.name else None,
            "timestamp": isoformat_or_none(message.timestamp),
            "latency_ms": message.latency_ms,
            "provider_meta": _strip_private_use_obj(message.provider_meta),
            "parts": [
                {
                    "kind": part.kind,
                    "text": strip_private_use(part.text) if part.text else None,
                    "language": strip_private_use(part.language) if part.language else None,
                    "tool_name": strip_private_use(part.tool_name) if part.tool_name else None,
                    "arguments": _strip_private_use_obj(part.arguments),
                    "output": _strip_private_use_obj(part.output),
                    "id": part.id,
                }
                for part in message.parts
            ],
        }
        for message in sorted(
            session.normalized_messages or [],
            key=lambda item: (item.timestamp.timestamp() if item.timestamp else float("-inf")),
            reverse=True,
        )
    ]
    data["normalization_diagnostics"] = (
        {
            "total_events": session.normalization_diagnostics.total_events,
            "parsed_events": session.normalization_diagnostics.parsed_events,
            "skipped_events": session.normalization_diagnostics.skipped_events,
            "warnings": [
                strip_private_use(warning)
                for warning in (session.normalization_diagnostics.warnings or [])
            ],
        }
        if session.normalization_diagnostics
        else None
    )
    return {"session": data}


def _strip_private_use_obj(value):
    if value is None:
        return None
    if isinstance(value, str):
        return strip_private_use(value)
    if isinstance(value, list):
        return [_strip_private_use_obj(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _strip_private_use_obj(item) for key, item in value.items()}
    return value


def _safe_write(handler: BaseHTTPRequestHandler, payload: bytes) -> None:
    try:
        handler.wfile.write(payload)
    except BrokenPipeError:
        # Client disconnected mid-response; nothing else to do.
        return


def send_json(
    handler: BaseHTTPRequestHandler,
    payload: dict[str, object],
    status: HTTPStatus = HTTPStatus.OK,
) -> None:
    data = json.dumps(payload).encode("utf-8")
    send_json_bytes(handler, data, status=status)


def send_json_bytes(
    handler: BaseHTTPRequestHandler,
    payload: bytes,
    status: HTTPStatus = HTTPStatus.OK,
) -> None:
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(payload)))
    handler.end_headers()
    _safe_write(handler, payload)


def serve_static_file(handler: BaseHTTPRequestHandler, static_root: Path, relative: str) -> bool:
    if not relative:
        return False
    try:
        resolved_root = static_root.resolve()
        target = (static_root / unquote(relative)).resolve()
    except (FileNotFoundError, OSError, ValueError):
        return False

    if not target.is_file() or not target.is_relative_to(resolved_root):
        return False

    content_type, _ = mimetypes.guess_type(target.name)
    if content_type is None:
        content_type = "application/octet-stream"

    try:
        data = target.read_bytes()
    except OSError:
        handler.send_error(HTTPStatus.INTERNAL_SERVER_ERROR, "Failed to read static file")
        return True

    handler.send_response(HTTPStatus.OK)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.end_headers()
    _safe_write(handler, data)
    return True


@dataclass
class _InFlightDetailPayload:
    event: threading.Event = field(default_factory=threading.Event)
    payload: dict[str, object] | None = None


class SessionApi:
    """Request handlers for API endpoints."""

    def __init__(self, service: SessionService) -> None:
        self.service = service
        self._detail_cache_lock = threading.Lock()
        self._detail_cache: OrderedDict[str, dict[str, object]] = OrderedDict()
        self._detail_inflight: dict[str, _InFlightDetailPayload] = {}

    def dispatch(self, handler: BaseHTTPRequestHandler, path: str, query: str) -> bool:
        started = time.perf_counter()
        endpoint = self._endpoint_name(path)
        handled = False

        try:
            params = parse_qs(query, keep_blank_values=False)
            if path == "/api/sessions":
                self.list_sessions(handler, params)
                handled = True
            elif path == "/api/search-hits":
                self.search_hits(handler, params)
                handled = True
            elif path.startswith("/api/sessions/"):
                self.session_detail(handler, path, params)
                handled = True
            elif path == "/api/providers":
                self.providers(handler)
                handled = True
            elif path == "/api/models":
                self.models(handler, params)
                handled = True
            elif path == "/api/working-dirs":
                self.working_dirs(handler)
                handled = True
            return handled
        finally:
            log_event(
                "http.endpoint",
                endpoint=endpoint,
                response_ms=(time.perf_counter() - started) * 1000,
                handled=handled,
            )

    def list_sessions(self, handler: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> None:
        page = self._coerce_positive_int(params.get("page", ["1"])[0], default=1)
        if page is None:
            send_json(handler, {"error": "Invalid page parameter"}, HTTPStatus.BAD_REQUEST)
            return

        page_size = self._coerce_positive_int(params.get("page_size", ["10"])[0], default=10)
        if page_size is None:
            send_json(handler, {"error": "Invalid page_size parameter"}, HTTPStatus.BAD_REQUEST)
            return
        page_size = min(page_size, MAX_PAGE_SIZE)

        order = params.get("order", [ORDER_UPDATED_AT])[0]
        if order not in SUPPORTED_ORDERS:
            send_json(
                handler,
                {
                    "error": "Unsupported order parameter",
                    "allowed": sorted(SUPPORTED_ORDERS),
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        session_query = self._build_session_query(params, order, page, page_size)
        page_result = self.service.list_sessions(session_query, max_page_size=MAX_PAGE_SIZE)

        payload = {
            "page": page_result.page,
            "page_size": page_result.page_size,
            "total_sessions": page_result.total,
            "total_pages": page_result.total_pages,
            "sessions": [session_summary(item) for item in page_result.items],
        }
        send_json(handler, payload)

    def search_hits(self, handler: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> None:
        raw_term = params.get("search", [""])[0]
        search_term = strip_private_use(raw_term).strip()
        if not search_term:
            send_json(handler, {"query": "", "hits": [], "has_more": False})
            return

        limit = self._coerce_positive_int(params.get("limit", ["8"])[0], default=8)
        if limit is None:
            send_json(handler, {"error": "Invalid limit parameter"}, HTTPStatus.BAD_REQUEST)
            return
        limit = min(limit, 50)

        order = params.get("order", [ORDER_UPDATED_AT])[0]
        if order not in SUPPORTED_ORDERS:
            send_json(
                handler,
                {
                    "error": "Unsupported order parameter",
                    "allowed": sorted(SUPPORTED_ORDERS),
                },
                HTTPStatus.BAD_REQUEST,
            )
            return

        session_query = self._build_session_query(params, order, 1, 1)
        normalized = session_query.normalized()
        normalized.search = strip_private_use(normalized.search).strip()
        lowered_term = normalized.search.lower()
        if not lowered_term:
            send_json(handler, {"query": "", "hits": [], "has_more": False})
            return

        sessions = self.service.all_sessions()
        filtered = apply_filters(sessions, normalized)
        ordered = sort_sessions(filtered, normalized.order)

        hits: list[dict[str, object]] = []
        has_more = False

        for session in ordered:
            if len(hits) >= limit:
                has_more = True
                break

            messages = _ordered_messages(session)
            for index, message in enumerate(messages):
                content = strip_private_use(message.content or "")
                if not content:
                    continue
                lowered = content.lower()
                match_start = lowered.find(lowered_term)
                if match_start == -1:
                    continue

                one_line = _to_one_line(content)
                snippet, snippet_start, snippet_length = _build_snippet(
                    one_line, match_start, len(lowered_term)
                )
                hits.append(
                    {
                        "provider": session.provider,
                        "session_id": session.session_id,
                        "source_path": str(session.source_path),
                        "message_index": index,
                        "match_start": match_start,
                        "match_length": len(lowered_term),
                        "snippet": snippet,
                        "snippet_match_start": snippet_start,
                        "snippet_match_length": snippet_length,
                    }
                )
                if len(hits) >= limit:
                    has_more = True
                    break

        send_json(handler, {"query": normalized.search, "hits": hits, "has_more": has_more})

    def session_detail(
        self, handler: BaseHTTPRequestHandler, path: str, params: dict[str, list[str]]
    ) -> None:
        segments = [segment for segment in path.split("/") if segment]
        if len(segments) < 3:
            send_json(handler, {"error": "Invalid session path"}, HTTPStatus.NOT_FOUND)
            return

        provider = unquote(segments[2])
        session_id = unquote("/".join(segments[3:])) if len(segments) > 3 else ""
        source_path = params.get("source_path", [None])[0]

        lookup_started = time.perf_counter()
        result = self.service.get_session_with_metrics(provider, session_id or None, source_path)
        lookup_ms = (time.perf_counter() - lookup_started) * 1000
        session = result.session
        if session is None:
            send_json(handler, {"error": "Session not found"}, HTTPStatus.NOT_FOUND)
            return

        payload_started = time.perf_counter()
        payload, detail_cache_status = self._detail_payload_for_session(session)
        payload_build_ms = (time.perf_counter() - payload_started) * 1000
        encoded = json.dumps(payload).encode("utf-8")
        log_event(
            "session.detail_load",
            provider=session.provider,
            session_id=session.session_id,
            source_path=str(session.source_path),
            lookup_source=result.source,
            lookup_cache_status=result.cache_status,
            endpoint_lookup_ms=lookup_ms,
            parse_normalize_ms=result.parse_ms,
            payload_build_ms=payload_build_ms,
            payload_cache_status=detail_cache_status,
            payload_bytes=len(encoded),
            message_count=session.message_count,
            normalized_count=len(session.normalized_messages or []),
        )
        send_json_bytes(handler, encoded)

    def providers(self, handler: BaseHTTPRequestHandler) -> None:
        sessions = self.service.all_sessions()
        summary: dict[str, ProviderSummary] = {
            entry.slug: ProviderSummary(
                id=entry.slug,
                label=entry.label,
                env_var=entry.env_var,
                default_paths=list(entry.default_paths),
                session_count=0,
                last_updated=None,
            )
            for entry in list_providers()
        }

        for session in sessions:
            entry_summary = summary.setdefault(
                session.provider,
                ProviderSummary(
                    id=session.provider,
                    label=provider_label(session.provider),
                    env_var=None,
                    default_paths=[],
                    session_count=0,
                    last_updated=None,
                ),
            )
            entry_summary["session_count"] += 1
            last_updated = session.updated_at or session.started_at
            if last_updated:
                last_iso = isoformat_or_none(last_updated)
                current = entry_summary["last_updated"]
                if last_iso and (current is None or last_iso > current):
                    entry_summary["last_updated"] = last_iso

        providers = sorted(summary.values(), key=lambda item: item["label"])
        send_json(handler, {"providers": providers})

    def models(self, handler: BaseHTTPRequestHandler, params: dict[str, list[str]]) -> None:
        sessions = self.service.all_sessions()
        provider_filters = {value for value in params.get("provider", []) if value}
        labels: dict[str, str] = {}
        counts: dict[str, int] = {}
        providers_by_model: dict[str, set[str]] = {}

        for session in sessions:
            if provider_filters and session.provider not in provider_filters:
                continue
            model = strip_private_use(session.model).strip() if session.model else ""
            if not model:
                continue

            key = model.casefold()
            labels.setdefault(key, model)
            counts[key] = counts.get(key, 0) + 1
            providers_by_model.setdefault(key, set()).add(session.provider)

        models: list[ModelSummary] = [
            ModelSummary(
                id=labels[key],
                label=labels[key],
                count=counts[key],
                providers=sorted(
                    providers_by_model.get(key, set()),
                    key=str.casefold,
                ),
            )
            for key in labels
        ]
        models.sort(key=lambda item: (-item["count"], item["label"].casefold()))
        send_json(handler, {"models": models})

    def working_dirs(self, handler: BaseHTTPRequestHandler) -> None:
        sessions = self.service.all_sessions()
        counts: dict[str, int] = {}
        for session in sessions:
            if not session.working_dir:
                continue
            path = strip_private_use(session.working_dir).strip()
            if not path:
                continue
            counts[path] = counts.get(path, 0) + 1

        sorted_counts = sorted(
            counts.items(),
            key=lambda item: (-item[1], item[0].casefold()),
        )
        payload = [{"path": path, "count": count} for path, count in sorted_counts]
        send_json(handler, {"working_dirs": payload})

    @staticmethod
    def _coerce_positive_int(value: str, default: int) -> int | None:
        try:
            return max(1, int(value))
        except ValueError:
            return None

    def _build_session_query(
        self,
        params: dict[str, list[str]],
        order: str,
        page: int,
        page_size: int,
    ) -> SessionQuery:
        provider_filters = {value for value in params.get("provider", []) if value}
        include_dirs = {value for value in params.get("include_working_dir", []) if value}
        exclude_dirs = {value for value in params.get("exclude_working_dir", []) if value}
        search_term = params.get("search", [""])[0].strip()
        model_exact = {value for value in params.get("model", []) if value}
        model_prefixes = {value for value in params.get("model_prefix", []) if value}
        model_provider = params.get("model_provider", [""])[0].strip() or None
        model_match = params.get("model_match", [""])[0].strip().lower()
        if model_match == "prefix" and model_exact and not model_prefixes:
            model_prefixes = set(model_exact)
            model_exact = set()

        return SessionQuery(
            providers=provider_filters,
            search=search_term,
            model_exact=model_exact,
            model_prefixes=model_prefixes,
            model_provider=model_provider,
            order=order,
            page=page,
            page_size=page_size,
            include_working_dirs=include_dirs,
            exclude_working_dirs=exclude_dirs,
        )

    @staticmethod
    def _endpoint_name(path: str) -> str:
        if path.startswith("/api/sessions/"):
            return "/api/sessions/:provider/:session"
        return path

    def _detail_payload_for_session(self, session: SessionRecord) -> tuple[dict[str, object], str]:
        key = self._detail_cache_key(session)
        owner = False

        with self._detail_cache_lock:
            cached = self._detail_cache.get(key)
            if cached is not None:
                self._detail_cache.move_to_end(key)
                return cached, "hit"

            inflight = self._detail_inflight.get(key)
            if inflight is None:
                inflight = _InFlightDetailPayload()
                self._detail_inflight[key] = inflight
                owner = True

        if not owner:
            inflight.event.wait()
            if inflight.payload is not None:
                return inflight.payload, "coalesced"
            payload = session_detail(session)
            return payload, "miss"

        try:
            payload = session_detail(session)
            inflight.payload = payload
            with self._detail_cache_lock:
                self._detail_cache[key] = payload
                self._detail_cache.move_to_end(key)
                while len(self._detail_cache) > DETAIL_CACHE_MAX:
                    self._detail_cache.popitem(last=False)
            return payload, "miss"
        finally:
            inflight.event.set()
            with self._detail_cache_lock:
                self._detail_inflight.pop(key, None)

    @staticmethod
    def _detail_cache_key(session: SessionRecord) -> str:
        fingerprint = path_fingerprint(session.source_path)
        if fingerprint:
            mtime_ns, size = fingerprint
            return (
                f"{session.provider}::{session.session_id}::{session.source_path}::"
                f"{mtime_ns}:{size}"
            )
        return (
            f"{session.provider}::{session.session_id}::{session.source_path}::"
            f"{isoformat_or_none(session.updated_at) or ''}:{session.message_count}"
        )


@dataclass
class SessionRouter:
    """Dispatch HTTP requests to API handlers or static assets."""

    api: SessionApi
    static_root: Path

    def dispatch(self, handler: BaseHTTPRequestHandler, parsed: ParseResult) -> bool:
        path = parsed.path or "/"
        if path.startswith("/api/"):
            return self.api.dispatch(handler, path, parsed.query)
        return self._dispatch_static(handler, path)

    def _dispatch_static(self, handler: BaseHTTPRequestHandler, path: str) -> bool:
        if path in ("/", "/index.html"):
            return serve_static_file(handler, self.static_root, "index.html")
        if path in ("/session", "/session.html"):
            return serve_static_file(handler, self.static_root, "session.html")
        if path.startswith("/static/"):
            relative = path[len("/static/") :]
            return serve_static_file(handler, self.static_root, relative)
        candidate = path.lstrip("/")
        if candidate:
            return serve_static_file(handler, self.static_root, candidate)
        return False


def create_request_handler(router: SessionRouter) -> type[BaseHTTPRequestHandler]:
    """Bind a router to a concrete BaseHTTPRequestHandler subclass."""

    class SessionRequestHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            handled = router.dispatch(self, parsed)
            if not handled:
                self.send_error(HTTPStatus.NOT_FOUND, "Not Found")

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            # Silence default logging; the GUI is typically run locally.
            return

    return SessionRequestHandler


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    refresh_interval: float | None = None,
) -> None:
    """
    Start the Agent Sessions HTTP server.

    Args:
        host: Hostname or IP to bind.
        port: TCP port to listen on.
        refresh_interval: Override the default caching interval (seconds). Set to
            0 to disable caching.
    """

    interval = DEFAULT_REFRESH_INTERVAL if refresh_interval is None else float(refresh_interval)
    service = SessionService(refresh_interval=interval)
    router = SessionRouter(api=SessionApi(service), static_root=STATIC_DIR)
    handler_cls = create_request_handler(router)

    server = ThreadingHTTPServer((host, port), handler_cls)
    print(f"Serving Agent Sessions at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        server.server_close()
