"""
Shared query primitives for filtering and sorting sessions.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from .model import SessionRecord
from .util import strip_private_use

ORDER_UPDATED_AT = "updated_at"
ORDER_STARTED_AT = "started_at"
ORDER_MESSAGES = "messages"
SUPPORTED_ORDERS = {
    ORDER_UPDATED_AT,
    ORDER_STARTED_AT,
    ORDER_MESSAGES,
}


@dataclass
class SessionQuery:
    providers: set[str] = field(default_factory=set)
    search: str = ""
    model_exact: set[str] = field(default_factory=set)
    model_prefixes: set[str] = field(default_factory=set)
    model_provider: str | None = None
    order: str = ORDER_UPDATED_AT
    page: int = 1
    page_size: int = 10
    include_working_dirs: set[str] = field(default_factory=set)
    exclude_working_dirs: set[str] = field(default_factory=set)

    def normalized(self, *, max_page_size: int | None = None) -> SessionQuery:
        providers = {provider for provider in self.providers if provider}
        search = (self.search or "").strip()
        model_exact = _normalize_model_values(self.model_exact)
        model_prefixes = _normalize_model_values(self.model_prefixes)
        model_provider = (self.model_provider or "").strip() or None

        order = self.order or ORDER_UPDATED_AT
        if order not in SUPPORTED_ORDERS:
            order = ORDER_UPDATED_AT

        page = self.page if self.page and self.page > 0 else 1
        page_size = self.page_size if self.page_size and self.page_size > 0 else 10
        if max_page_size is not None:
            page_size = min(page_size, max_page_size)

        include_dirs: set[str] = set()
        for value in self.include_working_dirs:
            if not value:
                continue
            cleaned = strip_private_use(value).strip()
            if cleaned:
                include_dirs.add(cleaned)

        exclude_dirs: set[str] = set()
        for value in self.exclude_working_dirs:
            if not value:
                continue
            cleaned = strip_private_use(value).strip()
            if cleaned:
                exclude_dirs.add(cleaned)
        exclude_dirs -= include_dirs

        return SessionQuery(
            providers=providers,
            search=search,
            model_exact=model_exact,
            model_prefixes=model_prefixes,
            model_provider=model_provider,
            order=order,
            page=page,
            page_size=page_size,
            include_working_dirs=include_dirs,
            exclude_working_dirs=exclude_dirs,
        )


@dataclass
class SessionPage:
    items: list[SessionRecord]
    total: int
    page: int
    page_size: int
    total_pages: int
    has_next: bool
    has_previous: bool


def matches_provider(session: SessionRecord, providers: set[str]) -> bool:
    if not providers:
        return True
    return session.provider in providers


def matches_working_dir(
    session: SessionRecord, include_dirs: set[str], exclude_dirs: set[str]
) -> bool:
    if not include_dirs and not exclude_dirs:
        return True

    working_dir = session.working_dir
    if working_dir:
        normalized = strip_private_use(working_dir).strip()
    else:
        normalized = ""

    if include_dirs:
        if not normalized or normalized not in include_dirs:
            return False
    if exclude_dirs and normalized and normalized in exclude_dirs:
        return False
    return True


def matches_search(session: SessionRecord, term: str) -> bool:
    if not term:
        return True
    lowered = term.lower()

    index = getattr(session, "search_index", None)
    if index is None:
        index = session.refresh_search_index()
    return index.matches(lowered)


def matches_model(
    session: SessionRecord,
    model_exact: set[str],
    model_prefixes: set[str],
    model_provider: str | None,
) -> bool:
    if model_provider and session.provider != model_provider:
        return False
    if not model_exact and not model_prefixes:
        return True

    model = _normalize_model_value(session.model)
    if not model:
        return False
    if model in model_exact:
        return True
    return any(model.startswith(prefix) for prefix in model_prefixes)


def _sort_key_started(session: SessionRecord) -> float:
    return session.started_at.timestamp() if session.started_at else float("-inf")


def _sort_key_messages(session: SessionRecord) -> float:
    return float(session.message_count)


def _sort_key_updated(session: SessionRecord) -> float:
    return session.updated_at.timestamp() if session.updated_at else float("-inf")


def sort_sessions(sessions: Sequence[SessionRecord], order: str) -> list[SessionRecord]:
    if order == ORDER_STARTED_AT:
        key_fn = _sort_key_started
    elif order == ORDER_MESSAGES:
        key_fn = _sort_key_messages
    else:
        key_fn = _sort_key_updated
    return sorted(sessions, key=key_fn, reverse=True)


def apply_filters(sessions: Iterable[SessionRecord], query: SessionQuery) -> list[SessionRecord]:
    return [
        session
        for session in sessions
        if matches_provider(session, query.providers)
        and matches_search(session, query.search)
        and matches_model(
            session,
            query.model_exact,
            query.model_prefixes,
            query.model_provider,
        )
        and matches_working_dir(session, query.include_working_dirs, query.exclude_working_dirs)
    ]


def _normalize_model_values(values: set[str]) -> set[str]:
    normalized: set[str] = set()
    for value in values:
        cleaned = _normalize_model_value(value)
        if cleaned:
            normalized.add(cleaned)
    return normalized


def _normalize_model_value(value: str | None) -> str:
    if not value:
        return ""
    return strip_private_use(value).strip().lower()
