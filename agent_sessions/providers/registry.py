"""Central registry for provider metadata."""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Iterable
from dataclasses import dataclass

from .base import SessionProvider
from .claude import ClaudeProvider
from .codex import CodexProvider
from .gemini import GeminiProvider


@dataclass(frozen=True)
class ProviderEntry:
    slug: str
    label: str
    provider_cls: type[SessionProvider]
    env_var: str | None
    default_paths: tuple[str, ...] = ()


PROVIDER_REGISTRY: OrderedDict[str, ProviderEntry] = OrderedDict(
    (
        (
            CodexProvider.name,
            ProviderEntry(
                slug=CodexProvider.name,
                label="codex",
                provider_cls=CodexProvider,
                env_var=CodexProvider.env_var,
                default_paths=("~/.codex/sessions",),
            ),
        ),
        (
            ClaudeProvider.name,
            ProviderEntry(
                slug=ClaudeProvider.name,
                label="claude",
                provider_cls=ClaudeProvider,
                env_var=ClaudeProvider.env_var,
                default_paths=("~/.claude/projects", "~/.claude/__store.db"),
            ),
        ),
        (
            GeminiProvider.name,
            ProviderEntry(
                slug=GeminiProvider.name,
                label="gemini",
                provider_cls=GeminiProvider,
                env_var=GeminiProvider.env_var,
                default_paths=(
                    "~/.gemini",
                    "~/.config/google-generative-ai",
                    "~/.local/share/google-generative-ai",
                    "%APPDATA%/google/generative-ai",
                ),
            ),
        ),
    )
)

DEFAULT_PROVIDERS = tuple(entry.provider_cls for entry in PROVIDER_REGISTRY.values())


def list_providers() -> Iterable[ProviderEntry]:
    return PROVIDER_REGISTRY.values()


def get_provider_entry(slug: str) -> ProviderEntry | None:
    return PROVIDER_REGISTRY.get(slug)
