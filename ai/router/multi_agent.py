"""
multi_agent.py

Execution mode "multi_agent": for a request that spans several
capabilities (e.g. "refactor this function AND explain the architectural
tradeoff"), each capability goes to its own specialist family — not every
model answering the same question (that's consensus_v2.py's job). Then a
synthesis pass combines the specialist outputs into one answer.
"""

import asyncio
import logging

from ai.router import model_catalog as cat
from ai.router.providers import complete, ProviderError, ProviderResponse

logger = logging.getLogger("ai_router.multi_agent")

SYNTHESIS_SYSTEM = (
    "You are combining outputs from several specialist engineers, each of "
    "whom handled a different aspect of the same task, into one coherent "
    "final answer for the developer."
)


async def _run_role(capability: str, family: str, quality_mode: cat.QualityTier,
                     role_system: str, prompt: str, max_tokens: int) -> tuple[str, ProviderResponse | Exception]:
    provider = cat.get_provider(family)
    model_id = cat.get_model_id(family, quality_mode)
    try:
        resp = await complete(provider, model_id, role_system, prompt, max_tokens)
        return capability, resp
    except ProviderError as e:
        return capability, e


async def run_multi_agent(capabilities: list[str], quality_mode: cat.QualityTier,
                           prompt: str, tier_access: str = "free",
                           max_tokens: int = 1024) -> dict:
    """
    capabilities: ordered list of capability labels this task touches
    (e.g. ["coding", "architecture"]), typically from the classifier.

    Each capability is assigned the best available family from
    MULTI_AGENT_CAPABILITY_MAP, walking down the ordered list if an
    earlier candidate's provider fails. Two different capabilities can
    end up on the same family if that's genuinely the best fit for both —
    that's fine, it just runs as two separate calls.
    """
    assignments: dict[str, str] = {}
    for capability in capabilities:
        candidates = cat.get_multi_agent_families(capability, quality_mode)
        # respect tier_access — filter candidates to ones this tier can use
        candidates = [f for f in candidates if f in cat.all_family_keys(tier_access=tier_access)]
        if candidates:
            assignments[capability] = candidates[0]

    if not assignments:
        raise RuntimeError(f"No available families for any of: {capabilities} at tier_access={tier_access}")

    tasks = []
    for capability, family in assignments.items():
        role_system = f"You are the specialist for '{capability}' on this task. Focus only on that aspect."
        tasks.append(_run_role(capability, family, quality_mode, role_system, prompt, max_tokens))

    results = await asyncio.gather(*tasks)

    successes = [(cap, resp) for cap, resp in results if not isinstance(resp, Exception)]
    failures = [(cap, resp) for cap, resp in results if isinstance(resp, Exception)]
    for cap, err in failures:
        logger.warning("Multi-agent role '%s' failed: %s", cap, err)

    if not successes:
        raise RuntimeError(f"All multi-agent roles failed: {failures}")

    sections = "\n\n".join(
        f"--- {cap} specialist ({resp.model_id}) ---\n{resp.text}" for cap, resp in successes
    )
    synthesis_prompt = f"Task:\n{prompt}\n\n{sections}\n\nCombine these into one final answer."
    synthesis = await complete(cat.BRAIN_PROVIDER, cat.BRAIN_MODEL_ID,
                                SYNTHESIS_SYSTEM, synthesis_prompt, max_tokens)

    return {
        "final_text": synthesis.text,
        "role_responses": {cap: resp for cap, resp in successes},
        "failed_roles": [cap for cap, _ in failures],
        "assignments": assignments,
        "mode": "multi_agent",
    }