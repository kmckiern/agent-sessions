"""
Utility helpers shared across providers and the UI.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

_PRIVATE_USE_TABLE = dict.fromkeys(range(0xE000, 0xF900), None)


def parse_timestamp(value: Any) -> datetime | None:
    """
    Convert assorted timestamp representations to timezone-aware datetime objects.

    Supports ISO8601 strings, unix epoch seconds, and milliseconds.
    """
    if value is None:
        return None

    if isinstance(value, int | float):
        seconds = float(value)
        if seconds > 1e12:  # treat as milliseconds
            seconds /= 1000.0
        try:
            return datetime.fromtimestamp(seconds, tz=timezone.utc)
        except (OverflowError, OSError):
            return None

    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        # Coerce trailing Z if missing timezone
        if cleaned.endswith("Z"):
            cleaned = cleaned[:-1] + "+00:00"
        try:
            return datetime.fromisoformat(cleaned)
        except ValueError:
            pass

    # unrecognised format
    return None


def stringify_content(content: Any) -> str:
    """
    Flatten content blobs from various provider formats into human readable text.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, int | float | bool):
        return str(content)
    if isinstance(content, dict):
        # Prefer known keys
        for key in ("text", "content", "value"):
            if key in content:
                return stringify_content(content[key])
        # fall back to joining nested values
        return " ".join(stringify_content(v) for v in content.values())
    if isinstance(content, Iterable):
        return " ".join(stringify_content(item) for item in content)
    return str(content)


def coalesce(*values: Any) -> Any | None:
    """
    Return the first non-empty value from the provided arguments.
    """
    for value in values:
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def strip_private_use(text: str | None) -> str:
    """Remove private-use Unicode characters (e.g., citation markers) from text."""
    if not text:
        return ""
    return text.translate(_PRIVATE_USE_TABLE)
