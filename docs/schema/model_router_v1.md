# Model router v1

Source of truth is the code in `ai/router/providers.py` and
`ai/router/router.py`.

## What this is

The piece that makes "free now, paid later" a `.env` change, not a
code change. `ai/narration/impact_narration.py` (and anything else in
`ai/` that needs to call a model) takes `llm_call: Callable[[str], str]`
as an injected dependency rather than importing this module directly —
`router.complete` is passed in as that callable, with zero adapter code.

## Providers — `providers.py`

Three, covering local/free-hosted/paid:

| Provider | Tier | Notes |
|---|---|---|
| `OllamaProvider` | Free, local | Needs Ollama running + model pulled. `is_available()` pings `/api/tags`. |
| `OpenAICompatibleProvider` | Free, hosted | Covers most free-tier APIs (Groq, others) that speak the OpenAI chat/completions shape — one implementation, different `base_url`/`api_key`/`model` per actual provider. |
| `AnthropicProvider` | Paid | Real, correctly-shaped call to `/v1/messages`. Gated by the router's config, not by this class itself. |

All three use `urllib.request` (stdlib) rather than adding
`requests`/`httpx` — same dependency-light choice made elsewhere in
this project (e.g. `tier1_generator`'s subprocess-based git calls).

**Honest limitation:** these three classes make real, correctly-shaped
HTTP calls but were **not tested against a live endpoint** — no local
Ollama, no free-tier key, no Anthropic key available where this was
built. What's thoroughly tested is the router's fallback logic
(below), which is the part with actual risk of bugs; the HTTP calls
themselves should be smoke-tested against whichever provider is set up
first.

## Router — `router.py`

`ModelRouter.complete(prompt) -> str` — tries each provider in list
order. Skips (doesn't call) any provider where `is_available()` is
`False`; falls through to the next if a provider is available but its
`complete()` call raises for any reason. Raises `AllProvidersFailedError`
(carrying every provider's specific failure reason) only if every
provider in the list failed or was unavailable.

`build_default_router()` — reads environment variables and builds the
standard **Ollama → free-tier → paid** priority chain:

```
OLLAMA_MODEL, OLLAMA_HOST
FREE_TIER_API_KEY, FREE_TIER_MODEL, FREE_TIER_BASE_URL
USE_PAID_MODELS=true|false   (default false)
ANTHROPIC_API_KEY, ANTHROPIC_MODEL
```

**The one safety property worth knowing:** the paid provider is only
ever added to the list if `USE_PAID_MODELS` is explicitly `"true"` —
an `ANTHROPIC_API_KEY` sitting in `.env` does **not** enable paid usage
by itself. Verified directly: a paid key present with
`USE_PAID_MODELS` unset produces a 2-provider router (Ollama + free
tier only); the paid provider only appears once the flag is flipped.

## Verified (fallback logic, with stub providers — not live network)

- Skips an unavailable provider, uses the next
- Falls through when a provider is available but the call itself fails
- Raises `AllProvidersFailedError` with per-provider reasons when
  everything fails
- Handles an empty provider list cleanly
- `router.complete` plugs directly into `narrate_impact()` with zero
  glue code

## Current status

- ✅ Providers implemented (not live-tested), router fallback logic
  fully tested, config-driven builder verified including the
  paid-tier safety property.
- ❌ No live test against any real model response yet — do this as
  soon as one provider (even just Ollama locally) is actually
  available, since this is the one thing that couldn't be verified
  here.