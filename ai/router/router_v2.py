"""
router_v2.py

Execution mode "single": one capability, one best family, with automatic
cross-provider fallback if the first choice's provider is down/rate-limited/erroring.

v2 changes (backward compatible: run_single keeps its signature, new args are optional):
  * the fallback order is: best family -> its FAMILY_FALLBACKS chain -> every remaining family
    by score. Before, the chain stopped after ~3 families (coding: qwen-coder -> gpt-oss -> deepseek)
    and never tried qwen, gemini or llama.
  * a (provider, model_id) pair is never tried twice. In the catalog, "llama" and "gpt-oss" resolve to
    the same openai/gpt-oss-* models, so a failed gpt-oss call used to be repeated under another name.
  * avoid_models: the caller can exclude models, e.g. one that just returned unparseable output.
  * families flagged unhealthy in MODEL_STATUS are skipped (unless that would leave nothing).
"""

import logging

from ai.router import model_catalog as cat
from ai.router.providers import complete, ProviderError, ProviderResponse

logger = logging.getLogger("ai_router.router_v2")


class AllFamiliesFailedError(Exception):
    pass


def pick_family(capability: str, tier_access: str = "free") -> str:
    """Highest-scoring family for this capability among families the
    caller's tier is allowed to use."""
    candidates = cat.all_family_keys(tier_access=tier_access)
    if not candidates:
        raise AllFamiliesFailedError(f"No families available for tier_access={tier_access}")
    return max(candidates, key=lambda fam: cat.get_scores(fam).get(capability, 0))


def candidate_order(capability: str, tier_access: str = "free") -> list[str]:
    """Best family, then its fallback chain, then everything else by score (no duplicates)."""
    pool = cat.all_family_keys(tier_access=tier_access)
    if not pool:
        raise AllFamiliesFailedError(f"No families available for tier_access={tier_access}")
    primary = pick_family(capability, tier_access)
    order = [primary]
    seen = {primary}
    nxt = cat.get_family_fallback(primary)
    while nxt is not None and nxt not in seen:
        seen.add(nxt)
        if nxt in pool:                      # never fall back into a family the tier may not use
            order.append(nxt)
        nxt = cat.get_family_fallback(nxt)
    rest = sorted((f for f in pool if f not in seen),
                  key=lambda f: cat.get_scores(f).get(capability, 0), reverse=True)
    return order + rest


def _healthy(family: str, quality_mode: cat.QualityTier) -> bool:
    try:
        key = cat.get_tracking_key(family, quality_mode)
    except KeyError:
        return True
    return cat.MODEL_STATUS.get(key, {}).get("healthy", True)


async def run_single(capability: str, quality_mode: cat.QualityTier, system: str,
                      prompt: str, tier_access: str = "free",
                      max_tokens: int = 1024,
                      avoid_models: set[str] | None = None,
                      temperature: float | None = None) -> ProviderResponse:
    avoid = avoid_models or set()
    order = candidate_order(capability, tier_access)
    healthy = [f for f in order if _healthy(f, quality_mode)]
    order = healthy or order                 # if everything is flagged unhealthy, try anyway

    tried: list[str] = []
    seen_models: set[tuple[str, str]] = set()
    for family in order:
        provider = cat.get_provider(family)
        model_id = cat.get_model_id(family, quality_mode)
        if model_id in avoid or (provider, model_id) in seen_models:
            continue
        seen_models.add((provider, model_id))
        tried.append(family)
        try:
            return await complete(provider, model_id, system, prompt, max_tokens, temperature=temperature)
        except ProviderError as e:
            logger.warning("Family %s (%s) failed (%s), trying next", family, model_id, e)

    raise AllFamiliesFailedError(f"All families failed for capability={capability} (tried: {tried})")