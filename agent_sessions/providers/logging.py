"""Lightweight debug logging helpers for providers."""

from __future__ import annotations

import os
import sys

DEBUG_ENABLED = os.getenv("AGENT_SESSIONS_DEBUG", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}


def debug_warning(message: str, exc: Exception | None = None) -> None:
    """
    Emit a warning message when AGENT_SESSIONS_DEBUG is enabled.

    We avoid configuring the global logging system and write directly to stderr
    to keep provider ingestion side-effect free during normal operation.
    """
    if not DEBUG_ENABLED:
        return
    if exc:
        print(f"[agent-sessions] {message}: {exc}", file=sys.stderr)
    else:
        print(f"[agent-sessions] {message}", file=sys.stderr)
