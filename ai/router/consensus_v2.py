"""
consensus_v2.py

Execution mode "consensus": two specialist families answer the SAME
prompt independently, then a synthesis pass (using BRAIN_MODEL_ID)
merges them into one answer — used when a task benefits from
cross-checking rather than delegation (multi_agent.py is for delegation).
"""

import asyncio
import logging

from ai.router import model_catalog as cat
from ai.router.providers import complete, ProviderError, ProviderResponse
from ai.router.router_v2 import AllFamiliesFailedError, run_single

logger = logging.getLogger("ai_router.consensus_v2")

SYNTHESIS_SYSTEM = (
    "You are combining two independent expert answers to the same question "
    "into one clear, correct final answer. Where they agree, state it "
    "plainly. Where they disagree, use your own judgment to resolve it and "
    "briefly note the disagreement existed."
)


async def _try_family(family: str, quality_mode: cat.QualityTier, system: str,
                       prompt: str, max_tokens: int) -> ProviderResponse:
    provider = cat.get_provider(family)
    model_id = cat.get_model_id(family, quality_mode)
    return await complete(provider, model_id, system, prompt, max_tokens)


async def run_consensus(capability: str, quality_mode: cat.QualityTier, system: str,
                         prompt: str, tier_access: str = "free",
                         max_tokens: int = 1024) -> dict:
    """
    Returns {"final_text": ..., "responses": [ProviderResponse, ProviderResponse]}
    Falls back to single-model execution if no consensus pair succeeds.
    """
    strategies = cat.get_consensus_strategy(capability)

    for pair in strategies:
        fam_a, fam_b = pair
        results = await asyncio.gather(
            _try_family(fam_a, quality_mode, system, prompt, max_tokens),
            _try_family(fam_b, quality_mode, system, prompt, max_tokens),
            return_exceptions=True,
        )
        if any(isinstance(r, Exception) for r in results):
            logger.warning("Consensus pair %s failed: %s", pair, results)
            continue

        resp_a, resp_b = results
        synthesis_prompt = (
            f"Question:\n{prompt}\n\n"
            f"--- Expert A ({resp_a.model_id}) ---\n{resp_a.text}\n\n"
            f"--- Expert B ({resp_b.model_id}) ---\n{resp_b.text}\n\n"
            f"Give the single best final answer."
        )
        synthesis = await complete(
            cat.BRAIN_PROVIDER, cat.BRAIN_MODEL_ID,
            SYNTHESIS_SYSTEM, synthesis_prompt, max_tokens,
        )
        return {"final_text": synthesis.text, "responses": [resp_a, resp_b],
                "mode": "consensus", "pair": pair}

    logger.warning("All consensus pairs failed for capability=%s, falling back to single", capability)
    fallback = await run_single(capability, quality_mode, system, prompt, tier_access, max_tokens)
    return {"final_text": fallback.text, "responses": [fallback], "mode": "single_fallback", "pair": None}