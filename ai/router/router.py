# """
# Model router — tries a list of providers in order, falls through on
# any that are unavailable or fail. This is the piece that makes the
# free-now/paid-later decision a config change, not a code change:
# whoever calls narrate_impact() or anything else in ai/ passes
# `router.complete` as the injected llm_call — see
# ai/narration/impact_narration.py's NarrationCall type.

# CONFIG-DRIVEN, VIA build_default_router():
# Reads from environment variables so the free/paid choice is a .env
# flip, not a code change:
#   USE_PAID_MODELS=true|false   (default: false)
#   OLLAMA_MODEL, OLLAMA_HOST     (default host: localhost:11434)
#   FREE_TIER_API_KEY, FREE_TIER_BASE_URL, FREE_TIER_MODEL
#   ANTHROPIC_API_KEY, ANTHROPIC_MODEL
# Priority order is always: Ollama -> hosted free-tier -> paid, and paid
# is skipped entirely unless USE_PAID_MODELS=true, regardless of whether
# a key is present — so an accidentally-set paid key doesn't silently
# start incurring cost.
# """

# from __future__ import annotations

# import os
# from dataclasses import dataclass, field

# from .providers import AnthropicProvider, ModelProvider, OllamaProvider, OpenAICompatibleProvider


# class AllProvidersFailedError(Exception):
#     """Raised when every provider in the router's list was either
#     unavailable or raised an error. Carries the per-provider failure
#     reasons so the caller (or narration's caller) can see why, rather
#     than getting an opaque failure."""

#     def __init__(self, attempts: list[tuple[str, str]]) -> None:
#         self.attempts = attempts
#         summary = "; ".join(f"{name}: {reason}" for name, reason in attempts)
#         super().__init__(f"All providers failed or were unavailable: {summary}")


# @dataclass
# class ModelRouter:
#     providers: list[ModelProvider] = field(default_factory=list)

#     def complete(self, prompt: str) -> str:
#         attempts: list[tuple[str, str]] = []
#         for provider in self.providers:
#             if not provider.is_available():
#                 attempts.append((provider.name, "not configured/unavailable"))
#                 continue
#             try:
#                 return provider.complete(prompt)
#             except Exception as e:  # noqa: BLE001 - deliberately broad, any
#                                       # provider failure should fall through
#                 attempts.append((provider.name, f"{type(e).__name__}: {e}"))
#         raise AllProvidersFailedError(attempts)


# def build_default_router() -> ModelRouter:
#     """Reads environment variables and constructs the standard
#     Ollama -> free-tier -> paid priority chain described in the module
#     docstring. Providers with no config present are still added to the
#     list — is_available() will correctly skip them at call time — so
#     this never raises just because a tier isn't configured yet.
#     """
#     providers: list[ModelProvider] = []

#     ollama_model = os.environ.get("OLLAMA_MODEL")
#     if ollama_model:
#         providers.append(OllamaProvider(
#             model=ollama_model,
#             host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
#         ))

#     free_tier_key = os.environ.get("FREE_TIER_API_KEY", "")
#     free_tier_model = os.environ.get("FREE_TIER_MODEL", "")
#     free_tier_url = os.environ.get("FREE_TIER_BASE_URL", "")
#     if free_tier_model and free_tier_url:
#         providers.append(OpenAICompatibleProvider(
#             model=free_tier_model, api_key=free_tier_key, base_url=free_tier_url,
#         ))

#     use_paid = os.environ.get("USE_PAID_MODELS", "false").lower() == "true"
#     if use_paid:
#         anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")
#         anthropic_model = os.environ.get("ANTHROPIC_MODEL", "")
#         if anthropic_model:
#             providers.append(AnthropicProvider(model=anthropic_model, api_key=anthropic_key))

#     return ModelRouter(providers=providers)