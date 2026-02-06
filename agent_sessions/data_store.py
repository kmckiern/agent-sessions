"""Caching and query orchestration for session discovery."""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .cache import (
    METADATA_SCHEMA_VERSION,
    DiskMetadataCache,
    DiskSessionCache,
    path_fingerprint,
)
from .indexer import build_providers, load_sessions
from .model import SessionRecord
from .providers import SessionProvider
from .providers.logging import debug_warning
from .query import SessionPage, SessionQuery, apply_filters, sort_sessions
from .telemetry import log_event

_TRUE_VALUES = {"1", "true", "yes", "on"}


def _strict_cache_mode() -> bool:
    return os.getenv("AGENT_SESSIONS_STRICT_CACHE", "").strip().lower() in _TRUE_VALUES


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


@dataclass(slots=True)
class SessionLoadResult:
    session: SessionRecord | None
    source: str
    parse_ms: float = 0.0
    cache_status: str = "miss"


@dataclass
class _InFlightDirectLoad:
    event: threading.Event = field(default_factory=threading.Event)
    result: SessionRecord | None = None
    parse_ms: float = 0.0
    cache_status: str = "miss"


class SessionService:
    """High-level gateway for cached session access and querying."""

    def __init__(
        self,
        providers: Sequence[SessionProvider] | None = None,
        refresh_interval: float | None = 5.0,
        *,
        clock=None,
    ) -> None:
        self._provider_overrides = list(providers) if providers is not None else None
        self._providers: list[SessionProvider] | None = (
            list(providers) if providers is not None else None
        )
        self._clock = clock or time.time
        self._lock = threading.Lock()
        self._refresh_condition = threading.Condition(self._lock)
        self._provider_io_lock = threading.Lock()

        self._sessions: list[SessionRecord] = []
        self._sessions_by_path: dict[str, SessionRecord] = {}
        self._sessions_by_provider_id: dict[tuple[str, str], SessionRecord] = {}
        self._manifest: dict[tuple[str, str], tuple[int, int]] = {}
        self._manifest_hash: str = ""
        self._cache_key: str | None = None

        self._cache_state = _CacheState(refresh_interval=refresh_interval)
        self._serve_stale_while_revalidate = (
            self._provider_overrides is None and not _strict_cache_mode()
        )
        self._bootstrapped_from_disk = False
        self._startup_validation_scheduled = False

        self._refresh_inflight = False
        self._direct_inflight: dict[str, _InFlightDirectLoad] = {}

        self._metadata_cache = DiskMetadataCache.from_env()
        self._direct_disk_cache = DiskSessionCache.from_env()
        self._direct_disk_cache_loaded = False

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

        total_pages = (total + normalized.page_size - 1) // normalized.page_size
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
        return self.get_session_with_metrics(provider, session_id, source_path).session

    def get_session_with_metrics(
        self,
        provider: str | None,
        session_id: str | None,
        source_path: str | None = None,
    ) -> SessionLoadResult:
        if not provider and not source_path:
            return SessionLoadResult(session=None, source="invalid")

        if source_path:
            direct = self._load_session_from_source_path_coalesced(
                source_path,
                session_id,
                provider,
            )
            if direct.session is not None:
                return direct

        self._ensure_snapshot_ready()
        session = self._lookup_cached_session(provider, session_id, source_path)
        if session is None:
            return SessionLoadResult(session=None, source="snapshot", cache_status="miss")
        return SessionLoadResult(session=session, source="snapshot", cache_status="hit")

    def invalidate(self) -> None:
        with self._lock:
            self._cache_state.mark_loaded(0.0)
            self._startup_validation_scheduled = False

    # Internal helpers -------------------------------------------------

    def _all_sessions(self) -> list[SessionRecord]:
        self._ensure_snapshot_ready()
        with self._lock:
            return list(self._sessions)

    def _ensure_snapshot_ready(self) -> None:
        blocking_reason: str | None = None
        background_reason: str | None = None

        with self._lock:
            if not self._sessions:
                self._bootstrap_from_disk_cache_locked()
                if not self._sessions:
                    blocking_reason = "startup_miss"
                elif self._cache_state.should_reload(True, now=self._clock):
                    if self._serve_stale_while_revalidate:
                        background_reason = "startup_refresh_interval"
                    else:
                        blocking_reason = "startup_refresh_interval"
                elif (
                    self._serve_stale_while_revalidate
                    and not self._startup_validation_scheduled
                ):
                    background_reason = "startup_validate"
                    self._startup_validation_scheduled = True
            elif self._cache_state.should_reload(True, now=self._clock):
                if self._serve_stale_while_revalidate:
                    background_reason = "refresh_interval"
                else:
                    blocking_reason = "refresh_interval"

        if blocking_reason:
            self._refresh_blocking(blocking_reason)
        elif background_reason:
            self._refresh_async(background_reason)

    def _bootstrap_from_disk_cache_locked(self) -> None:
        if self._bootstrapped_from_disk:
            return
        self._bootstrapped_from_disk = True

        providers = self._providers_locked()
        cache_key = self._compute_cache_key(providers)
        self._cache_key = cache_key

        started = time.perf_counter()
        snapshot = self._metadata_cache.load(cache_key)
        load_ms = (time.perf_counter() - started) * 1000

        if snapshot is None:
            log_event(
                "startup.metadata_cache",
                status="miss",
                cache_read_ms=load_ms,
            )
            return

        self._apply_snapshot_locked(
            sessions=snapshot.sessions,
            manifest=snapshot.manifest,
            manifest_hash=snapshot.manifest_hash,
            cache_key=cache_key,
        )
        log_event(
            "startup.metadata_cache",
            status="hit",
            cache_read_ms=load_ms,
            sessions=len(snapshot.sessions),
        )

    def _refresh_async(self, reason: str) -> None:
        with self._lock:
            if self._refresh_inflight:
                return
            self._refresh_inflight = True
        thread = threading.Thread(target=self._refresh_worker, args=(reason,), daemon=True)
        thread.start()

    def _refresh_blocking(self, reason: str) -> None:
        with self._lock:
            if self._refresh_inflight:
                while self._refresh_inflight:
                    self._refresh_condition.wait()
                return
            self._refresh_inflight = True
        self._refresh_worker(reason)

    def _refresh_worker(self, reason: str) -> None:
        try:
            self._refresh_snapshot(reason)
        finally:
            with self._lock:
                self._refresh_inflight = False
                self._refresh_condition.notify_all()

    def _refresh_snapshot(self, reason: str) -> None:
        providers = self._providers_for_refresh()
        cache_key = self._compute_cache_key(providers)

        manifest_started = time.perf_counter()
        with self._provider_io_lock:
            manifest = self._build_manifest(providers)
        manifest_ms = (time.perf_counter() - manifest_started) * 1000
        manifest_hash = self._manifest_hash_for(manifest)

        with self._lock:
            previous_manifest_hash = self._manifest_hash
            has_sessions = bool(self._sessions)
            previous_cache_key = self._cache_key
            self._cache_key = cache_key

        cache_key_changed = previous_cache_key is not None and previous_cache_key != cache_key
        manifest_verifiable = bool(manifest)
        manifest_changed = manifest_verifiable and manifest_hash != previous_manifest_hash
        should_rebuild = cache_key_changed or not has_sessions
        if not should_rebuild:
            if not manifest_verifiable:
                should_rebuild = True
            else:
                should_rebuild = manifest_changed

        if not should_rebuild:
            with self._lock:
                self._manifest = manifest
                self._manifest_hash = manifest_hash
                self._cache_state.mark_loaded(self._clock())
            log_event(
                "startup.cache_decision",
                status="hit",
                reason=reason,
                rebuild_ms=0.0,
                manifest_ms=manifest_ms,
            )
            return

        rebuild_started = time.perf_counter()
        sessions = self._load_sessions_with_cache(providers)
        rebuild_ms = (time.perf_counter() - rebuild_started) * 1000

        with self._lock:
            self._apply_snapshot_locked(
                sessions=sessions,
                manifest=manifest,
                manifest_hash=manifest_hash,
                cache_key=cache_key,
            )

        write_started = time.perf_counter()
        self._metadata_cache.persist(cache_key, manifest_hash, manifest, sessions)
        cache_write_ms = (time.perf_counter() - write_started) * 1000

        status = "miss" if not has_sessions else "stale"
        log_event(
            "startup.cache_decision",
            status=status,
            reason=reason,
            rebuild_ms=rebuild_ms,
            manifest_ms=manifest_ms,
            cache_write_ms=cache_write_ms,
            sessions=len(sessions),
        )

    def _load_sessions_with_cache(
        self,
        providers: Sequence[SessionProvider],
    ) -> list[SessionRecord]:
        disk_cache = DiskSessionCache.from_env()

        cache_read_started = time.perf_counter()
        disk_cache.load()
        cache_read_ms = (time.perf_counter() - cache_read_started) * 1000

        for provider in providers:
            provider.attach_cache(disk_cache)

        index_started = time.perf_counter()
        sessions = load_sessions(providers)
        index_ms = (time.perf_counter() - index_started) * 1000

        cache_write_started = time.perf_counter()
        disk_cache.persist()
        cache_write_ms = (time.perf_counter() - cache_write_started) * 1000

        log_event(
            "startup.index_load",
            cache_read_ms=cache_read_ms,
            index_load_ms=index_ms,
            cache_write_ms=cache_write_ms,
            sessions=len(sessions),
        )
        return sessions

    def _load_session_from_source_path_coalesced(
        self,
        source_path: str,
        session_id: str | None,
        provider: str | None,
    ) -> SessionLoadResult:
        key = f"{provider or '*'}::{source_path}::{session_id or ''}"
        owner = False
        with self._lock:
            inflight = self._direct_inflight.get(key)
            if inflight is None:
                inflight = _InFlightDirectLoad()
                self._direct_inflight[key] = inflight
                owner = True

        if not owner:
            inflight.event.wait()
            return SessionLoadResult(
                session=inflight.result,
                source="direct-coalesced",
                parse_ms=inflight.parse_ms,
                cache_status=inflight.cache_status,
            )

        try:
            started = time.perf_counter()
            record = self._load_session_from_source_path(source_path, session_id, provider)
            parse_ms = (time.perf_counter() - started) * 1000
            inflight.result = record
            inflight.parse_ms = parse_ms
            inflight.cache_status = "hit" if record is not None else "miss"
            if record is not None:
                with self._lock:
                    self._upsert_session_locked(record)
            return SessionLoadResult(
                session=record,
                source="direct",
                parse_ms=parse_ms,
                cache_status=inflight.cache_status,
            )
        finally:
            inflight.event.set()
            with self._lock:
                self._direct_inflight.pop(key, None)

    def _load_session_from_source_path(
        self,
        source_path: str,
        session_id: str | None,
        provider: str | None,
    ) -> SessionRecord | None:
        providers = self._providers_for_lookup()
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
            with self._provider_io_lock:
                record = provider.load_session_from_source_path(source_path, session_id)
        except Exception as exc:
            debug_warning(
                f"Provider {provider.name} failed direct load for {source_path}",
                exc,
            )
            log_event(
                "session.direct_load",
                provider=provider.name,
                status="error",
                source_path=source_path,
                error=str(exc),
            )
            return None
        if record is not None:
            log_event(
                "session.direct_load",
                provider=provider.name,
                status="hit",
                source_path=source_path,
            )
        return record

    def _lookup_cached_session(
        self,
        provider: str | None,
        session_id: str | None,
        source_path: str | None,
    ) -> SessionRecord | None:
        with self._lock:
            if provider and session_id:
                record = self._sessions_by_provider_id.get((provider, session_id))
                if record is not None:
                    if not source_path or str(record.source_path) == source_path:
                        return record
            if source_path:
                record = self._sessions_by_path.get(source_path)
                if record is not None:
                    return record
            return None

    def _providers_locked(self) -> list[SessionProvider]:
        if self._providers is None:
            self._providers = build_providers()
        return self._providers

    def _providers_for_lookup(self) -> list[SessionProvider]:
        with self._lock:
            providers = list(self._providers_locked())
            self._ensure_direct_disk_cache_loaded_locked()
            cache = self._direct_disk_cache
        for provider in providers:
            provider.attach_cache(cache)
        return providers

    def _providers_for_refresh(self) -> list[SessionProvider]:
        # For default operation we create fresh providers per refresh to avoid
        # carrying stale provider internals across refresh cycles.
        if self._provider_overrides is None:
            return build_providers()
        return self._providers_for_lookup()

    def _ensure_direct_disk_cache_loaded_locked(self) -> None:
        if self._direct_disk_cache_loaded:
            return
        started = time.perf_counter()
        self._direct_disk_cache.load()
        self._direct_disk_cache_loaded = True
        log_event(
            "session.direct_cache_read",
            cache_read_ms=(time.perf_counter() - started) * 1000,
        )

    def _build_manifest(
        self, providers: Sequence[SessionProvider]
    ) -> dict[tuple[str, str], tuple[int, int]]:
        manifest: dict[tuple[str, str], tuple[int, int]] = {}
        for provider in providers:
            try:
                paths = provider.cache_validation_paths()
            except Exception as exc:
                debug_warning(f"Provider {provider.name} failed to enumerate cache paths", exc)
                continue
            for path in paths:
                canonical = _canonical_path(path)
                if canonical is None:
                    continue
                fingerprint = path_fingerprint(canonical)
                if fingerprint is None:
                    continue
                manifest[(provider.name, str(canonical))] = fingerprint
        return manifest

    @staticmethod
    def _manifest_hash_for(manifest: dict[tuple[str, str], tuple[int, int]]) -> str:
        hasher = hashlib.sha256()
        for (provider, source_path), (mtime_ns, size) in sorted(manifest.items()):
            hasher.update(
                f"{provider}\0{source_path}\0{mtime_ns}\0{size}\n".encode("utf-8", "ignore")
            )
        return hasher.hexdigest()

    @staticmethod
    def _compute_cache_key(providers: Sequence[SessionProvider]) -> str:
        payload = {
            "schema_version": METADATA_SCHEMA_VERSION,
            "providers": [
                {
                    "name": provider.name,
                    "module": provider.__class__.__module__,
                    "class": provider.__class__.__qualname__,
                    "base_dir": str(provider.base_dir.expanduser()),
                    "glob_patterns": list(getattr(provider, "glob_patterns", ())),
                    "env_var": provider.env_var,
                    "env_value": os.getenv(provider.env_var, "") if provider.env_var else "",
                }
                for provider in sorted(providers, key=lambda item: item.name)
            ],
        }
        raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()

    def _apply_snapshot_locked(
        self,
        *,
        sessions: list[SessionRecord],
        manifest: dict[tuple[str, str], tuple[int, int]],
        manifest_hash: str,
        cache_key: str,
    ) -> None:
        self._sessions = list(sessions)
        self._manifest = dict(manifest)
        self._manifest_hash = manifest_hash
        self._cache_key = cache_key
        self._cache_state.mark_loaded(self._clock())

        by_path: dict[str, SessionRecord] = {}
        by_provider_id: dict[tuple[str, str], SessionRecord] = {}
        for record in self._sessions:
            by_path[str(record.source_path)] = record
            by_provider_id[(record.provider, record.session_id)] = record
        self._sessions_by_path = by_path
        self._sessions_by_provider_id = by_provider_id

    def _upsert_session_locked(self, record: SessionRecord) -> None:
        source_key = str(record.source_path)
        provider_key = (record.provider, record.session_id)
        existing = self._sessions_by_provider_id.get(provider_key)

        if existing is None:
            self._sessions.append(record)
        else:
            try:
                index = self._sessions.index(existing)
            except ValueError:
                self._sessions.append(record)
            else:
                self._sessions[index] = record

        self._sessions_by_provider_id[provider_key] = record
        self._sessions_by_path[source_key] = record


def _canonical_path(path: Path) -> Path | None:
    try:
        return path.expanduser().resolve(strict=False)
    except OSError:
        return None
