"""
entitlement_bridge.py

The ONLY file that connects entitlements/ (trial/subscription state) to
this router. Maps a ServiceTier string (from entitlements/config.py's
PLAN_TO_ROUTER_TIER) to this catalog's quality_mode + tier_access.

When you add PREMIUM_FAMILIES to model_catalog.py, flip
"startup_top" -> tier_access="premium" below and nothing else changes.
"""

from ai.router.model_catalog import QualityTier

TIER_TO_QUALITY_MODE: dict[str, QualityTier] = {
    "free": "economy",
    "startup_medium": "balanced",
    "startup_top": "quality",
}

# tier_access filters which families are even eligible (see
# all_family_keys() in model_catalog.py). Everything is "free" today —
# flip startup_top to "premium" the day PREMIUM_FAMILIES is populated.
TIER_TO_ACCESS: dict[str, str] = {
    "free": "free",
    "startup_medium": "free",
    "startup_top": "free",   # -> change to "premium" once high-level models are added
}


def resolve(service_tier: str) -> tuple[QualityTier, str]:
    """Returns (quality_mode, tier_access) for a router.py ServiceTier string."""
    quality_mode = TIER_TO_QUALITY_MODE.get(service_tier, "economy")
    tier_access = TIER_TO_ACCESS.get(service_tier, "free")
    return quality_mode, tier_access