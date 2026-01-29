"""Test configuration including import path adjustments."""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def _isolate_disk_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Avoid writing to the user's real cache directory during tests."""
    monkeypatch.setenv("AGENT_SESSIONS_CACHE_DIR", str(tmp_path / "agent-sessions-cache"))
    monkeypatch.delenv("AGENT_SESSIONS_DISABLE_DISK_CACHE", raising=False)
