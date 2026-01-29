"""Coverage for the provider registry metadata."""

from __future__ import annotations

from agent_sessions.providers.registry import (
    DEFAULT_PROVIDERS,
    PROVIDER_REGISTRY,
    get_provider_entry,
    list_providers,
)


def test_registry_exposes_known_providers() -> None:
    entry = get_provider_entry("openai-codex")
    assert entry is not None
    assert entry.label == "codex"
    assert entry.env_var == "CODEX_HOME"


def test_default_providers_follow_registry_order() -> None:
    registry_slugs = [entry.slug for entry in list_providers()]
    provider_slugs = [provider.name for provider in DEFAULT_PROVIDERS]
    assert provider_slugs == registry_slugs


def test_registry_is_ordered() -> None:
    keys = list(PROVIDER_REGISTRY.keys())
    assert keys == [entry.slug for entry in list_providers()]
