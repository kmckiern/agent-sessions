"""
Base provider abstraction for session loaders.
"""

from __future__ import annotations

import os
from collections.abc import Iterable, Iterator, Sequence
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from ..model import SessionRecord
from .ingest import JsonlReader, SessionBuilder, iter_paths

if TYPE_CHECKING:
    from ..cache import DiskSessionCache


class SessionProvider:
    """Abstract base class for a session provider."""

    name: str = "unknown"
    env_var: str | None = None
    home_subdir: str | None = None
    glob_patterns: Sequence[str] = ()
    sort_descending: bool = True

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = base_dir or self.default_base_dir()
        self._cache: DiskSessionCache | None = None

    @classmethod
    def default_base_dir(cls) -> Path:
        if cls.env_var:
            env_home = os.getenv(cls.env_var)
            if env_home:
                return Path(env_home).expanduser()
        if cls.home_subdir:
            return Path.home() / cls.home_subdir
        msg = f"{cls.__name__} must define env_var/home_subdir or override default_base_dir()"
        raise NotImplementedError(msg)

    def sessions(self) -> Iterable[SessionRecord]:
        records = list(self._collect_sessions())
        records.extend(self.extra_sessions())
        return self._sorted(records)

    def load_session_from_source_path(
        self,
        source_path: str,
        session_id: str | None,
    ) -> SessionRecord | None:
        """Optional direct-load hook for a single session path."""
        return None

    def attach_cache(self, cache: DiskSessionCache | None) -> None:
        self._cache = cache

    def extra_sessions(self) -> Iterable[SessionRecord]:
        """Optional hook for subclasses to append additional sessions."""
        return ()

    def session_paths(self) -> Iterable[Path]:
        """Paths considered for session ingestion."""
        if not self.glob_patterns:
            return ()
        return iter_paths(self.base_dir, self.glob_patterns)

    def cache_validation_paths(self) -> Iterable[Path]:
        """
        Paths that define cache freshness for this provider.

        Defaults to transcript/session files discovered by ``session_paths``.
        Providers with extra non-transcript sources can override this method.
        """
        return self.session_paths()

    def iter_events(self, path: Path) -> Iterator[dict]:
        """Override to customise event iteration for a path."""
        return iter(JsonlReader(path))

    def session_id_from_path(self, path: Path) -> str:
        return path.stem

    def create_builder(self, path: Path) -> SessionBuilder:
        return SessionBuilder(
            provider=self.name,
            source_path=path,
            session_id=self.session_id_from_path(path),
        )

    def handle_event(self, builder: SessionBuilder, event: dict) -> None:
        """Process an individual event. Subclasses must implement."""
        raise NotImplementedError

    def post_process(self, record: SessionRecord) -> SessionRecord | None:
        return record

    def sort_key(self, record: SessionRecord) -> float:
        dt = record.updated_at or record.started_at
        return dt.timestamp() if isinstance(dt, datetime) else float("-inf")

    def _collect_sessions(self) -> Iterator[SessionRecord]:
        for path in self.session_paths():
            record = self._build_session_from_path_cached(path)
            if not record:
                continue
            processed = self.post_process(record)
            if processed:
                yield processed

    def _build_session_from_path_cached(self, path: Path) -> SessionRecord | None:
        cache = self._cache
        if cache:
            record = cache.lookup(self.name, path)
            if record:
                return record
        record = self._build_session_from_path(path)
        if record and cache:
            cache.store(self.name, path, record)
        return record

    def _build_session_from_path(self, path: Path) -> SessionRecord | None:
        builder = self.create_builder(path)
        for event in self.iter_events(path):
            if isinstance(event, dict):
                self.handle_event(builder, event)
        return builder.build()

    def _sorted(self, records: Iterable[SessionRecord]) -> list[SessionRecord]:
        return sorted(records, key=self.sort_key, reverse=self.sort_descending)
