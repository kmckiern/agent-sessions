"""Tests for query helpers used by the session service."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from agent_sessions.model import Message, NormalizedMessage, NormalizedPart, SessionRecord
from agent_sessions.query import (
    ORDER_MESSAGES,
    ORDER_UPDATED_AT,
    SessionQuery,
    apply_filters,
    matches_model,
    matches_provider,
    matches_search,
    matches_working_dir,
    sort_sessions,
)


def make_session(
    session_id: str,
    *,
    provider: str = "openai-codex",
    started_at: datetime | None,
    updated_at: datetime | None,
    messages: list[Message] | None = None,
    working_dir: str | None = "/work",
    model: str | None = "model",
) -> SessionRecord:
    return SessionRecord(
        provider=provider,
        session_id=session_id,
        source_path=Path(f"/tmp/{session_id}.jsonl"),
        started_at=started_at,
        updated_at=updated_at,
        working_dir=working_dir,
        model=model,
        messages=messages or [],
    )


def test_session_query_normalizes_defaults() -> None:
    query = SessionQuery(
        providers={"", "claude-code"},
        search="  demo  ",
        model_exact={" GPT-5 ", "", "\uf8ffgpt-4o"},
        model_prefixes={" claude-", ""},
        model_provider="  openai-codex ",
        order="unknown",
        page=-5,
        page_size=-10,
    )
    normalized = query.normalized(max_page_size=50)

    assert normalized.providers == {"claude-code"}
    assert normalized.search == "demo"
    assert normalized.model_exact == {"gpt-5", "gpt-4o"}
    assert normalized.model_prefixes == {"claude-"}
    assert normalized.model_provider == "openai-codex"
    assert normalized.order == ORDER_UPDATED_AT
    assert normalized.page == 1
    assert normalized.page_size == 10


def test_session_query_normalizes_working_dirs() -> None:
    query = SessionQuery(
        include_working_dirs={"  /repo ", "", "\uf8fe/tmp"},
        exclude_working_dirs={" /tmp  ", ""},
    )
    normalized = query.normalized()

    assert normalized.include_working_dirs == {"/repo", "/tmp"}
    assert normalized.exclude_working_dirs == set()


def test_matches_search_scans_messages() -> None:
    timestamp = datetime(2025, 10, 7, 15, tzinfo=timezone.utc)
    session = make_session(
        "s1",
        started_at=timestamp,
        updated_at=timestamp,
        messages=[Message(role="assistant", content="Hello world", created_at=timestamp)],
    )

    assert matches_search(session, "hello")
    assert not matches_search(session, "absent")


def test_matches_search_scans_normalized_parts() -> None:
    timestamp = datetime(2025, 10, 7, 15, tzinfo=timezone.utc)
    session = SessionRecord(
        provider="openai-codex",
        session_id="s1",
        source_path=Path("/tmp/s1.jsonl"),
        started_at=timestamp,
        updated_at=timestamp,
        working_dir="/work",
        model="model",
        messages=[],
        normalized_messages=[
            NormalizedMessage(
                id="m1",
                role="assistant",
                timestamp=timestamp,
                parts=[
                    NormalizedPart(kind="text", text="hello from normalized"),
                    NormalizedPart(kind="code", text="print('keyword')", language="python"),
                ],
            )
        ],
    )

    assert matches_search(session, "keyword")


def test_session_record_builds_normalized_search_index() -> None:
    timestamp = datetime(2025, 10, 7, 15, tzinfo=timezone.utc)
    session = make_session(
        "S1",
        provider="OpenAI\ue000",
        started_at=timestamp,
        updated_at=timestamp,
        working_dir=" /Repo ",
        messages=[
            Message(role="assistant", content="HeLLo\uf8ff WORLD", created_at=timestamp),
        ],
    )

    index = session.search_index
    assert index.provider == "openai"
    assert index.session_id == "s1"
    assert index.working_dir == " /repo "
    assert index.messages == ("hello world",)


def test_matches_search_rebuilds_missing_index() -> None:
    timestamp = datetime(2025, 10, 7, 15, tzinfo=timezone.utc)
    session = make_session(
        "s1",
        started_at=timestamp,
        updated_at=timestamp,
        messages=[Message(role="assistant", content="Hello world", created_at=timestamp)],
    )

    del session.search_index

    assert matches_search(session, "hello")


def test_matches_provider_filters_by_set() -> None:
    session = make_session(
        "s1",
        provider="claude-code",
        started_at=None,
        updated_at=None,
    )
    assert matches_provider(session, set())
    assert matches_provider(session, {"claude-code"})
    assert not matches_provider(session, {"openai-codex"})


def test_matches_model_supports_exact_and_prefix() -> None:
    timestamp = datetime(2025, 10, 7, 15, tzinfo=timezone.utc)
    session = make_session(
        "s1",
        provider="openai-codex",
        started_at=timestamp,
        updated_at=timestamp,
        model="GPT-5-Codex",
    )

    assert matches_model(session, {"gpt-5-codex"}, set(), None)
    assert matches_model(session, set(), {"gpt-5"}, None)
    assert not matches_model(session, {"gpt-4o"}, set(), None)
    assert not matches_model(session, set(), {"claude"}, None)


def test_matches_model_applies_optional_provider_filter() -> None:
    timestamp = datetime(2025, 10, 7, 15, tzinfo=timezone.utc)
    session = make_session(
        "s1",
        provider="openai-codex",
        started_at=timestamp,
        updated_at=timestamp,
        model="gpt-5-codex",
    )

    assert matches_model(session, set(), set(), "openai-codex")
    assert not matches_model(session, set(), set(), "claude-code")


def test_matches_working_dir_handles_include_and_exclude() -> None:
    timestamp = datetime(2025, 10, 7, 15, tzinfo=timezone.utc)
    session = make_session(
        "s1",
        provider="claude-code",
        started_at=timestamp,
        updated_at=timestamp,
        working_dir="/repo",
    )
    no_dir_session = make_session(
        "s2",
        provider="claude-code",
        started_at=timestamp,
        updated_at=timestamp,
        working_dir=None,
    )

    assert matches_working_dir(session, set(), set())
    assert matches_working_dir(session, {"/repo"}, set())
    assert not matches_working_dir(session, {"/other"}, set())
    assert not matches_working_dir(session, set(), {"/repo"})
    assert not matches_working_dir(session, {"/repo"}, {"/repo"})

    assert not matches_working_dir(no_dir_session, {"/repo"}, set())
    assert matches_working_dir(no_dir_session, set(), {"/repo"})


def test_sort_sessions_uses_message_count() -> None:
    timestamp = datetime(2025, 10, 7, 15, tzinfo=timezone.utc)
    sessions = [
        make_session(
            "few",
            started_at=timestamp,
            updated_at=timestamp,
            messages=[Message(role="user", content="hi", created_at=timestamp)],
        ),
        make_session(
            "many",
            started_at=timestamp,
            updated_at=timestamp,
            messages=[
                Message(role="user", content="hi", created_at=timestamp),
                Message(role="assistant", content="hello", created_at=timestamp),
            ],
        ),
    ]

    ordered = sort_sessions(sessions, ORDER_MESSAGES)
    assert [session.session_id for session in ordered] == ["many", "few"]


def test_apply_filters_combines_all_predicates() -> None:
    timestamp = datetime(2025, 10, 7, 15, tzinfo=timezone.utc)
    sessions = [
        make_session(
            "match",
            provider="gemini-cli",
            started_at=timestamp,
            updated_at=timestamp,
            model="gemini-2.0-pro",
            messages=[Message(role="assistant", content="Keyword", created_at=timestamp)],
        ),
        make_session(
            "other",
            provider="openai-codex",
            started_at=timestamp,
            updated_at=timestamp,
            model="gpt-5-codex",
        ),
    ]
    query = SessionQuery(
        providers={"gemini-cli"},
        search="keyword",
        model_prefixes={"gemini"},
    )
    filtered = apply_filters(sessions, query)
    assert [session.session_id for session in filtered] == ["match"]


def test_apply_filters_respects_working_dir_include() -> None:
    timestamp = datetime(2025, 10, 7, 15, tzinfo=timezone.utc)
    sessions = [
        make_session(
            "include-me",
            provider="codex",
            started_at=timestamp,
            updated_at=timestamp,
            working_dir="/projects/a",
        ),
        make_session(
            "exclude-me",
            provider="codex",
            started_at=timestamp,
            updated_at=timestamp,
            working_dir="/projects/b",
        ),
    ]
    query = SessionQuery(include_working_dirs={"/projects/a"})
    filtered = apply_filters(sessions, query)
    assert [session.session_id for session in filtered] == ["include-me"]


def test_apply_filters_respects_working_dir_exclude() -> None:
    timestamp = datetime(2025, 10, 7, 15, tzinfo=timezone.utc)
    sessions = [
        make_session(
            "keep",
            provider="codex",
            started_at=timestamp,
            updated_at=timestamp,
            working_dir="/projects/a",
        ),
        make_session(
            "drop",
            provider="codex",
            started_at=timestamp,
            updated_at=timestamp,
            working_dir="/projects/b",
        ),
    ]
    query = SessionQuery(exclude_working_dirs={"/projects/b"})
    filtered = apply_filters(sessions, query)
    assert [session.session_id for session in filtered] == ["keep"]
