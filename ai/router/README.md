# ai/router/ v2 — built on your real MiniAI catalog

This replaces the earlier placeholder-catalog design from this chat.
Everything here uses your actual production model IDs, families, scores,
fallbacks, and consensus strategies — ported 1:1 from backend's catalog.py.

## Files, in dependency order
```
model_catalog.py     -> your real catalog, unchanged data (+ PREMIUM_FAMILIES hook)
providers.py          -> real Groq/Google/OpenRouter API calls
router_v2.py           -> execution_mode "single": best family + fallback chain
consensus_v2.py        -> execution_mode "consensus": 2 models + synthesis
multi_agent.py          -> execution_mode "multi_agent": per-capability specialists + synthesis
classifier.py           -> scores capability/complexity/execution_mode for freeform requests
entitlement_bridge.py    -> maps entitlements' ServiceTier -> quality_mode + tier_access
orchestrator.py           -> the one entrypoint the rest of knowledge-engine calls
```

## Two ways to call it

**Known Engineer Mode features** (dead-code explain, impact narration —
fixed shape, skip the classifier to save tokens/latency):
```python
from orchestrator import run_known_task

result = await run_known_task(
    task_type="dead_code_explain",
    service_tier=tier,   # "free" / "startup_medium" / "startup_top" from entitlements
    system="...",         # your existing prompt-building logic
    prompt=prompt_with_real_source_code,
)
narration_text = result["final_text"]
```
`KNOWN_TASKS` in orchestrator.py currently maps:
- `dead_code_explain` -> coding, single
- `impact_narration` -> analysis, single
- `deep_repo_analysis` -> architecture, consensus (2-model cross-check)

Add a new feature = one new row in that dict.

**Open-ended / future general repo Q&A** (don't know the shape ahead of time):
```python
from orchestrator import run_freeform
result = await run_freeform(service_tier=tier, system="...", prompt=user_question)
```
This calls the classifier first, then routes to single/consensus/multi_agent
based on what it recommends.

## What I verified in the sandbox (no real API key here, so up to the API boundary)
- `pick_family()` correctly picks the real top-scoring family per capability
  (coding -> qwen-coder, research -> gemini, reasoning -> deepseek — matches
  your catalog's actual scores)
- `get_family_fallback()` returns the right cross-provider fallback
  (qwen-coder -> gpt-oss, gemini -> None)
- `entitlement_bridge.resolve()` maps all three tiers correctly
  (free->economy, startup_medium->balanced, startup_top->quality)
- `get_model_id()` respects quality_mode correctly (economy vs quality
  pull different real model strings from qwen-coder's tiers)
- `providers.complete()` fails cleanly with `ProviderError` when no key is
  set, rather than crashing — confirms the dispatch plumbing is sound

**You still need to do, on your machine, with your real keys:** run one
actual `run_known_task("dead_code_explain", ...)` call against a real
finding and confirm real text comes back. That's the one thing I can't
verify from here without your GROQ_API_KEY/GOOGLE_API_KEY/OPENROUTER_API_KEY.

## Adding a high-level (premium) model later
1. Add it to `PREMIUM_FAMILIES` in model_catalog.py (same shape as `FAMILIES`
   entries, `"tier_access": "premium"`)
2. Flip `TIER_TO_ACCESS["startup_top"]` to `"premium"` in entitlement_bridge.py
3. Nothing else changes — `pick_family()`, `get_multi_agent_families()`, and
   consensus strategies all read from the combined pool automatically

## Deliberately not done yet
- Real per-repo prompt engineering (system prompts still generic placeholders
  from the caller side — orchestrator doesn't build them, your narration
  modules do)
- Cost/usage tracking against `MODEL_COSTS` (data's there, nothing consumes
  it yet)
- `MODEL_STATUS` health tracking isn't wired to actually mark a family
  unhealthy after repeated failures — currently every call re-tries fresh