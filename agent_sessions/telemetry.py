"""Structured debug telemetry for cache and timing instrumentation."""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

_DEBUG_ENV_VALUES = {"1", "true", "yes", "on"}


def telemetry_enabled() -> bool:
    return os.getenv("AGENT_SESSIONS_DEBUG", "").strip().lower() in _DEBUG_ENV_VALUES


def log_event(event: str, **fields: Any) -> None:
    if not telemetry_enabled():
        return

    payload: dict[str, Any] = {
        "event": event,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    payload.update({key: _normalize_field(value) for key, value in fields.items()})
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True), file=sys.stderr)


def _normalize_field(value: Any) -> Any:
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Exception):
        return str(value)
    return value
