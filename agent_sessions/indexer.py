"""
Utilities for discovering and aggregating sessions across providers.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .model import SessionRecord
from .providers import DEFAULT_PROVIDERS, SessionProvider
from .providers.logging import debug_warning


@dataclass
class ProviderConfig:
    provider_cls: type[SessionProvider]
    base_dir: Path | None = None


def build_providers(
    configs: Sequence[ProviderConfig] | None = None,
) -> list[SessionProvider]:
    if configs is None:
        return [cls() for cls in DEFAULT_PROVIDERS]

    instances: list[SessionProvider] = []
    for config in configs:
        instances.append(config.provider_cls(config.base_dir))
    return instances


def load_sessions(
    providers: Iterable[SessionProvider] | None = None,
) -> list[SessionRecord]:
    """
    Collect sessions from all providers, sorted by most recent activity.
    """
    if providers is None:
        providers = build_providers()

    records: list[SessionRecord] = []
    for provider in providers:
        try:
            records.extend(provider.sessions())
        except Exception as exc:
            debug_warning(f"Provider {provider.name} failed to load sessions", exc)
            # Protect the aggregate view from single provider failures.
            continue

    return sorted(
        records,
        key=_record_timestamp,
        reverse=True,
    )


def _record_timestamp(record: SessionRecord) -> float:
    if record.updated_at:
        return record.updated_at.timestamp()
    if record.started_at:
        return record.started_at.timestamp()
    return float("-inf")
