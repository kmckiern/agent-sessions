"""
Provider registry for supported terminal AI agents.
"""

from .base import SessionProvider
from .claude import ClaudeProvider
from .codex import CodexProvider
from .gemini import GeminiProvider
from .registry import (
    DEFAULT_PROVIDERS,
    PROVIDER_REGISTRY,
    ProviderEntry,
    get_provider_entry,
    list_providers,
)

__all__ = [
    "SessionProvider",
    "CodexProvider",
    "ClaudeProvider",
    "GeminiProvider",
    "ProviderEntry",
    "PROVIDER_REGISTRY",
    "DEFAULT_PROVIDERS",
    "get_provider_entry",
    "list_providers",
]
