"""Caching and query orchestration for session discovery."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .cache import DiskSessionCache
from .indexer import build_providers, load_sessions
from .model import SessionRecord
from .providers import SessionProvider
from .providers.logging import debug_warning
from .query import SessionPage, SessionQuery, apply_filters, sort_sessions


@dataclass
class _CacheState:
    refresh_interval: float | None
    last_loaded: float = 0.0

    def should_reload(self, has_sessions: bool, *, now) -> bool:
        if not has_sessions:
            return True
        if self.refresh_interval is None:
            return False
        if self.refresh_interval <= 0:
            return True
        return (now() - self.last_loaded) > self.refresh_interval

    def mark_loaded(self, timestamp: float) -> None:
        self.last_loaded = timestamp


class SessionService:
    """High-level gateway for cached session access and querying."""

    def __init__(
        self,
        providers: Sequence[SessionProvider] | None = None,
        refresh_interval: float | None = 5.0,
        *,
        clock=None,
    ) -> None:
        self._providers = list(providers) if providers is not None else None
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._sessions: list[SessionRecord] = []
        self._cache_state = _CacheState(refresh_interval=refresh_interval)

    def list_sessions(
        self, query: SessionQuery, *, max_page_size: int | None = None
    ) -> SessionPage:
        normalized = query.normalized(max_page_size=max_page_size)
        sessions = self._all_sessions()
        filtered = apply_filters(sessions, normalized)
        ordered = sort_sessions(filtered, normalized.order)

        total = len(ordered)
        if total == 0:
            return SessionPage(
                items=[],
                total=0,
                page=1,
                page_size=normalized.page_size,
                total_pages=0,
                has_next=False,
                has_previous=False,
            )

        total_pages = math.ceil(total / normalized.page_size)
        page = min(normalized.page, total_pages)
        start = (page - 1) * normalized.page_size
        end = start + normalized.page_size
        items = ordered[start:end]

        return SessionPage(
            items=items,
            total=total,
            page=page,
            page_size=normalized.page_size,
            total_pages=total_pages,
            has_next=page < total_pages,
            has_previous=page > 1,
        )

    def all_sessions(self) -> list[SessionRecord]:
        return self._all_sessions()

    def get_session(
        self,
        provider: str | None,
        session_id: str | None,
        source_path: str | None = None,
    ) -> SessionRecord | None:
        if not provider and not source_path:
            return None

        if source_path:
            record = self._load_session_from_source_path(source_path, session_id, provider)
            if record is not None:
                return record

        for session in self._all_sessions():
            if provider and session_id:
                if session.provider == provider and session.session_id == session_id:
                    if not source_path or str(session.source_path) == source_path:
                        return session
            if source_path and str(session.source_path) == source_path:
                return session
        return None

    def invalidate(self) -> None:
        with self._lock:
            self._cache_state.mark_loaded(0.0)

    # Internal helpers -------------------------------------------------

    def _all_sessions(self) -> list[SessionRecord]:
        with self._lock:
            self._ensure_cache_locked()
            return list(self._sessions)

    def _ensure_cache_locked(self) -> None:
        if not self._cache_state.should_reload(bool(self._sessions), now=self._clock):
            return

        providers: Iterable[SessionProvider]
        if self._providers is None:
            providers = build_providers()
        else:
            providers = self._providers

        disk_cache = DiskSessionCache.from_env()
        disk_cache.load()
        for provider in providers:
            provider.attach_cache(disk_cache)

        self._sessions = load_sessions(providers)
        disk_cache.persist()
        self._cache_state.mark_loaded(self._clock())

    def _load_session_from_source_path(
        self,
        source_path: str,
        session_id: str | None,
        provider: str | None,
    ) -> SessionRecord | None:
        providers: Iterable[SessionProvider]
        if self._providers is None:
            providers = build_providers()
        else:
            providers = self._providers

        if provider:
            candidate = next((item for item in providers if item.name == provider), None)
            if candidate is None:
                return None
            return self._try_direct_load(candidate, source_path, session_id)

        for candidate in providers:
            record = self._try_direct_load(candidate, source_path, session_id)
            if record is not None:
                return record
        return None

    def _try_direct_load(
        self,
        provider: SessionProvider,
        source_path: str,
        session_id: str | None,
    ) -> SessionRecord | None:
        try:
            return provider.load_session_from_source_path(source_path, session_id)
        except Exception as exc:
            debug_warning(
                f"Provider {provider.name} failed direct load for {source_path}",
                exc,
            )
            return None
