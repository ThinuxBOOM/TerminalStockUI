"""AI provider package (Milestone 4). One module per vendor, one interface."""

from __future__ import annotations

from backend.ai.providers.anthropic import AnthropicProvider
from backend.ai.providers.base import (
    BaseProvider,
    build_stub_opinion,
    get_default_secret_store,
    set_default_secret_store,
)
from backend.ai.providers.gemini import GeminiProvider
from backend.ai.providers.openai import OpenAIProvider
from backend.ai.providers.xai import XAIProvider

PROVIDER_CLASSES: dict[str, type[BaseProvider]] = {
    GeminiProvider.name: GeminiProvider,
    OpenAIProvider.name: OpenAIProvider,
    AnthropicProvider.name: AnthropicProvider,
    XAIProvider.name: XAIProvider,
}


def build_default_providers(
    secret_store=None, model_overrides: dict[str, str] | None = None
) -> dict[str, BaseProvider]:
    """Instantiate one provider per vendor (stub until keys are configured)."""
    overrides = model_overrides or {}
    providers: dict[str, BaseProvider] = {}
    for name, cls in PROVIDER_CLASSES.items():
        model = overrides.get(name)
        providers[name] = cls(model=model, secret_store=secret_store)
    return providers


__all__ = [
    "PROVIDER_CLASSES",
    "AnthropicProvider",
    "BaseProvider",
    "GeminiProvider",
    "OpenAIProvider",
    "XAIProvider",
    "build_default_providers",
    "build_stub_opinion",
    "get_default_secret_store",
    "set_default_secret_store",
]
