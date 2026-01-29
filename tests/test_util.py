"""Tests for utility helper functions."""

from datetime import datetime, timezone

import pytest
from agent_sessions.util import coalesce, parse_timestamp, stringify_content


class TestParseTimestamp:
    def test_parses_iso8601_with_z_suffix(self) -> None:
        result = parse_timestamp("2024-06-01T08:15:30Z")
        assert result == datetime(2024, 6, 1, 8, 15, 30, tzinfo=timezone.utc)

    def test_parses_unix_seconds(self) -> None:
        result = parse_timestamp(1_700_000_000)
        assert result == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)

    def test_parses_milliseconds(self) -> None:
        result = parse_timestamp(1_700_000_000_000)
        assert result == datetime.fromtimestamp(1_700_000_000, tz=timezone.utc)

    @pytest.mark.parametrize("value", [None, "", "not-a-date"])
    def test_returns_none_for_invalid_inputs(self, value: object) -> None:
        assert parse_timestamp(value) is None


class TestStringifyContent:
    def test_returns_string_inputs(self) -> None:
        assert stringify_content("hello") == "hello"

    def test_flattens_dict_with_known_keys(self) -> None:
        assert stringify_content({"text": "hi"}) == "hi"

    def test_joins_iterables(self) -> None:
        content = ["first", {"content": "second"}, 3]
        assert stringify_content(content) == "first second 3"

    def test_handles_none(self) -> None:
        assert stringify_content(None) == ""


class TestCoalesce:
    def test_returns_first_non_empty_value(self) -> None:
        assert coalesce(None, " ", "value", "later") == "value"

    def test_returns_none_when_all_empty(self) -> None:
        assert coalesce(None, "", "   ") is None
