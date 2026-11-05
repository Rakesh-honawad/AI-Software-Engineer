"""
orchestrator.py

The single entrypoint for the rest of knowledge-engine.

Two ways in:

1. run_known_task() — for features where we already know the capability
   needed (dead-code explain = "coding", impact narration = "analysis").
   Skips the classifier entirely — cheaper, faster, deterministic. Use
   this for every Engineer Mode feature that has a fixed shape.

2. run_freeform() — for open-ended requests (future general repo Q&A)
   where we don't know ahead of time what capability/mode fits. Runs the
   classifier first, then dispatches based on what it recommends.

Both funnel into the same three execution-mode functions
(router_v2.run_single / consensus_v2.run_consensus / multi_agent.run_multi_agent).

v2: run_known_task accepts avoid_models (single mode only) so a caller can re-ask a
different model after one returned unusable output.
"""

import logging

from ai.router import model_catalog as cat
from ai.router.entitlement_bridge import resolve
from ai.router.router_v2 import run_single, AllFamiliesFailedError
from ai.router.consensus_v2 import run_consensus
from ai.router.multi_agent import run_multi_agent
from ai.router.classifier import classify

logger = logging.getLogger("ai_router.orchestrator")

# Fixed capability + execution mode per known Engineer Mode task type.
# Add a new row here for each new feature with a known shape — no other
# file needs to change.
KNOWN_TASKS: dict[str, dict] = {
    "dead_code_explain":  {"capability": "coding",       "mode": "single"},
    "impact_narration":   {"capability": "analysis",     "mode": "single"},
    "deep_repo_analysis": {"capability": "architecture",  "mode": "consensus"},
    "explain_node":        {"capability": "coding",   "mode": "single"},
    "repo_qa_plan":        {"capability": "analysis", "mode": "single"},
    "repo_qa_synthesize":  {"capability": "analysis", "mode": "single"},
    "bug_triage":          {"capability": "analysis", "mode": "single"},
    "patch_generate":      {"capability": "coding",   "mode": "single"},
}


async def run_known_task(task_type: str, service_tier: str, system: str,
                          prompt: str, max_tokens: int = 1024,
                          avoid_models: set[str] | None = None,
                          temperature: float | None = None) -> dict:
    if task_type not in KNOWN_TASKS:
        raise KeyError(f"Unknown task_type '{task_type}' — add it to KNOWN_TASKS in orchestrator.py")

    spec = KNOWN_TASKS[task_type]
    quality_mode, tier_access = resolve(service_tier)

    if spec["mode"] == "single":
        resp = await run_single(spec["capability"], quality_mode, system, prompt,
                                 tier_access=tier_access, max_tokens=max_tokens,
                                 avoid_models=avoid_models, temperature=temperature)
        return {"final_text": resp.text, "mode": "single", "model_id": resp.model_id}

    if spec["mode"] == "consensus":
        return await run_consensus(spec["capability"], quality_mode, system, prompt,
                                    tier_access=tier_access, max_tokens=max_tokens)

    raise NotImplementedError(f"Mode {spec['mode']} not wired for known tasks yet")


async def run_freeform(service_tier: str, system: str, prompt: str,
                        max_tokens: int = 1024) -> dict:
    quality_mode, tier_access = resolve(service_tier)
    result = await classify(prompt)
    logger.info("Classified: top=%s mode=%s confidence=%.2f",
                result.top_capability, result.execution_mode, result.confidence)

    if result.execution_mode == "multi_agent" and len(result.top_capabilities) > 1:
        return await run_multi_agent(result.top_capabilities, quality_mode, prompt,
                                      tier_access=tier_access, max_tokens=max_tokens)

    if result.execution_mode == "consensus":
        return await run_consensus(result.top_capability, quality_mode, system, prompt,
                                    tier_access=tier_access, max_tokens=max_tokens)

    resp = await run_single(result.top_capability, quality_mode, system, prompt,
                             tier_access=tier_access, max_tokens=max_tokens)
    return {"final_text": resp.text, "mode": "single", "model_id": resp.model_id,
            "classified_capability": result.top_capability}