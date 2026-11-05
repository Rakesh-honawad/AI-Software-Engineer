"""
providers.py

Real API calls, no mocks. One function per provider, all normalized to the
same return shape. router_v2.py / consensus_v2.py / multi_agent.py never
touch an SDK directly — only call `complete(provider, model_id, ...)`.

Env vars needed (same three keys you already use in backend/):
    GROQ_API_KEY
    GOOGLE_API_KEY
    OPENROUTER_API_KEY

v2 changes (the router only falls back on ProviderError, so every failure must become one):
  * complete() converts ANY exception into ProviderError: a missing SDK (ImportError), a Google
    response with no text (resp.text raises ValueError when blocked/empty), an empty `choices` list.
    Before, those escaped the try/except and killed the fallback chain.
  * hard timeout on every provider call (PROVIDER_TIMEOUT_S). Groq and Google had none, so a hung
    request would block the whole investigation instead of falling back.
  * an empty/None answer is a failure (reasoning models can use up max_tokens and return nothing).
  * optional `temperature` (None = provider default, so existing callers behave exactly as before).
"""

import asyncio
import os
import time
import logging
from dataclasses import dataclass

logger = logging.getLogger("ai_router.providers")

PROVIDER_TIMEOUT_S = 90


@dataclass
class ProviderResponse:
    text: str
    model_id: str
    provider: str
    latency_ms: float
    raw_usage: dict | None = None


class ProviderError(Exception):
    """Any provider-side failure: rate limit, auth, timeout, bad response shape."""


def provider_available(provider: str) -> bool:
    key_env = {
        "groq": "GROQ_API_KEY",
        "google": "GOOGLE_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
    }.get(provider)
    return bool(key_env and os.getenv(key_env))


async def _call_groq(model_id: str, system: str, prompt: str, max_tokens: int,
                      temperature: float | None = None) -> ProviderResponse:
    from groq import AsyncGroq
    client = AsyncGroq(api_key=os.getenv("GROQ_API_KEY"))
    extra = {} if temperature is None else {"temperature": temperature}
    start = time.monotonic()
    try:
        resp = await client.chat.completions.create(
            model=model_id,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            **extra,
        )
        text = resp.choices[0].message.content
        usage = getattr(resp, "usage", None) and resp.usage.model_dump()
    except Exception as e:
        raise ProviderError(f"Groq call failed for {model_id}: {e}") from e
    return ProviderResponse(
        text=text, model_id=model_id, provider="groq",
        latency_ms=(time.monotonic() - start) * 1000, raw_usage=usage,
    )


async def _call_google(model_id: str, system: str, prompt: str, max_tokens: int,
                        temperature: float | None = None) -> ProviderResponse:
    import google.generativeai as genai
    genai.configure(api_key=os.getenv("GOOGLE_API_KEY"))
    gmodel = genai.GenerativeModel(model_id, system_instruction=system)
    config = {"max_output_tokens": max_tokens}
    if temperature is not None:
        config["temperature"] = temperature
    start = time.monotonic()
    try:
        resp = await gmodel.generate_content_async(prompt, generation_config=config)
        text = resp.text          # raises ValueError when the answer was blocked or has no text part
    except Exception as e:
        raise ProviderError(f"Google call failed for {model_id}: {e}") from e
    return ProviderResponse(
        text=text, model_id=model_id, provider="google",
        latency_ms=(time.monotonic() - start) * 1000,
    )


async def _call_openrouter(model_id: str, system: str, prompt: str, max_tokens: int,
                            temperature: float | None = None) -> ProviderResponse:
    import httpx
    body = {
        "model": model_id,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    if temperature is not None:
        body["temperature"] = temperature
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {os.getenv('OPENROUTER_API_KEY')}"},
                json=body,
            )
            r.raise_for_status()
            data = r.json()
        text = data["choices"][0]["message"]["content"]
    except Exception as e:
        raise ProviderError(f"OpenRouter call failed for {model_id}: {e}") from e
    return ProviderResponse(
        text=text, model_id=model_id, provider="openrouter",
        latency_ms=(time.monotonic() - start) * 1000,
        raw_usage=data.get("usage"),
    )


_DISPATCH = {"groq": _call_groq, "google": _call_google, "openrouter": _call_openrouter}


async def complete(provider: str, model_id: str, system: str, prompt: str,
                    max_tokens: int = 1024, temperature: float | None = None) -> ProviderResponse:
    if provider not in _DISPATCH:
        raise ProviderError(f"Unknown/unimplemented provider: {provider}")
    if not provider_available(provider):
        raise ProviderError(f"No API key configured for provider: {provider}")
    try:
        resp = await asyncio.wait_for(
            _DISPATCH[provider](model_id, system, prompt, max_tokens, temperature),
            timeout=PROVIDER_TIMEOUT_S,
        )
    except ProviderError:
        raise
    except asyncio.TimeoutError as e:
        raise ProviderError(f"{provider} timed out after {PROVIDER_TIMEOUT_S}s for {model_id}") from e
    except Exception as e:  # noqa: BLE001  (missing SDK, odd response shape, ...)
        raise ProviderError(f"{provider} call failed for {model_id}: {type(e).__name__}: {e}") from e
    if not isinstance(resp.text, str) or not resp.text.strip():
        raise ProviderError(f"{provider} returned an empty answer for {model_id}")
    return resp