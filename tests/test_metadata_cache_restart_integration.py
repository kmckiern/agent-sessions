from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


def _run_restart_harness(*, cwd: Path, env: dict[str, str], source: Path, marker: Path) -> None:
    script = textwrap.dedent(
        """
        from __future__ import annotations

        import os
        from datetime import datetime, timezone
        from pathlib import Path

        from agent_sessions.data_store import SessionService
        from agent_sessions.model import Message, SessionRecord
        from agent_sessions.providers.base import SessionProvider

        source = Path(os.environ["AS_SOURCE"])
        marker = Path(os.environ["AS_MARKER"])


        class MarkerProvider(SessionProvider):
            name = "marker"

            def __init__(self, source_path: Path, marker_path: Path) -> None:
                self._source = source_path
                self._marker = marker_path
                super().__init__(base_dir=source_path.parent)

            def sessions(self):
                self._marker.parent.mkdir(parents=True, exist_ok=True)
                with self._marker.open("a", encoding="utf-8") as handle:
                    handle.write("call\\n")
                started = datetime(2026, 1, 13, tzinfo=timezone.utc)
                return [
                    SessionRecord(
                        provider=self.name,
                        session_id="s1",
                        source_path=self._source,
                        started_at=started,
                        updated_at=started,
                        working_dir="/workspace",
                        model="model",
                        messages=[Message(role="user", content="hi", created_at=started)],
                    )
                ]

            def cache_validation_paths(self):
                return [self._source]


        service = SessionService(
            providers=[MarkerProvider(source, marker)],
            refresh_interval=None,
        )
        service.all_sessions()
        """
    )

    subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(cwd),
        env={
            **env,
            "AS_SOURCE": str(source),
            "AS_MARKER": str(marker),
        },
        check=True,
        capture_output=True,
        text=True,
    )


def test_restart_uses_snapshot_with_xdg_fallback(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    source = workspace / "session.jsonl"
    source.write_text('{"event":"x"}\n', encoding="utf-8")
    marker = workspace / "calls.txt"

    primary = workspace / "unwritable-primary"
    primary.write_text("not a dir", encoding="utf-8")
    xdg_cache_home = workspace / "xdg"
    xdg_cache_home.mkdir(parents=True, exist_ok=True)
    expected_cache_path = xdg_cache_home / "agent-sessions" / "metadata_snapshot.json"

    env = os.environ.copy()
    project_root = str(Path(__file__).resolve().parents[1])
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{project_root}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else project_root
    )
    env["AGENT_SESSIONS_CACHE_DIR"] = str(primary)
    env["XDG_CACHE_HOME"] = str(xdg_cache_home)
    env.pop("AGENT_SESSIONS_DISABLE_DISK_CACHE", None)

    _run_restart_harness(cwd=workspace, env=env, source=source, marker=marker)
    assert marker.read_text(encoding="utf-8").splitlines() == ["call"]
    assert expected_cache_path.exists()

    _run_restart_harness(cwd=workspace, env=env, source=source, marker=marker)
    assert marker.read_text(encoding="utf-8").splitlines() == ["call"]


def test_restart_uses_snapshot_with_workspace_fallback(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    source = workspace / "session.jsonl"
    source.write_text('{"event":"x"}\n', encoding="utf-8")
    marker = workspace / "calls.txt"

    primary = workspace / "unwritable-primary"
    primary.write_text("not a dir", encoding="utf-8")
    xdg_cache_home = workspace / "xdg-not-a-dir"
    xdg_cache_home.write_text("not a dir", encoding="utf-8")
    expected_cache_path = workspace / ".agent-sessions-cache" / "metadata_snapshot.json"

    env = os.environ.copy()
    project_root = str(Path(__file__).resolve().parents[1])
    existing_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        f"{project_root}{os.pathsep}{existing_pythonpath}" if existing_pythonpath else project_root
    )
    env["AGENT_SESSIONS_CACHE_DIR"] = str(primary)
    env["XDG_CACHE_HOME"] = str(xdg_cache_home)
    env.pop("AGENT_SESSIONS_DISABLE_DISK_CACHE", None)

    _run_restart_harness(cwd=workspace, env=env, source=source, marker=marker)
    assert marker.read_text(encoding="utf-8").splitlines() == ["call"]
    assert expected_cache_path.exists()

    _run_restart_harness(cwd=workspace, env=env, source=source, marker=marker)
    assert marker.read_text(encoding="utf-8").splitlines() == ["call"]
