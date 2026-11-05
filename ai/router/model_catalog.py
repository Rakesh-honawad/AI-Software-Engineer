# ─────────────────────────────────────────────────────
# knowledge-engine/ai/router/model_catalog.py
#
# Ported 1:1 from MiniAI backend's V2 catalog so both systems stay in
# sync on model IDs, scores, and fallback rules. If backend's catalog
# changes (new model, deprecation), copy the change here too — this
# is intentionally a duplicate, not an import, so knowledge-engine
# stays a standalone library (per the existing architecture decision)
# and doesn't create a circular dependency with backend/.
#
# ⚠️ DEPRECATION TRACKER — re-verify before any change:
#   https://console.groq.com/docs/deprecations
# ─────────────────────────────────────────────────────

from typing import Literal

QualityTier = Literal["economy", "balanced", "quality"]

CAPABILITIES = [
    "coding", "reasoning", "math", "research",
    "writing", "analysis", "architecture", "general",
]

FAMILIES: dict = {
    "llama": {
        "provider": "groq",
        "tiers": {
            "economy":  {"model_id": "openai/gpt-oss-20b",  "tracking_key": "gpt-oss-20b"},
            "balanced": {"model_id": "openai/gpt-oss-20b",  "tracking_key": "gpt-oss-20b"},
            "quality":  {"model_id": "openai/gpt-oss-120b", "tracking_key": "gpt-oss-120b"},
        },
        "scores": {"coding": 78, "reasoning": 75, "math": 70, "research": 65,
                   "writing": 80, "analysis": 72, "architecture": 68, "general": 85},
        "primary_capabilities": ["general", "writing", "reasoning"],
        "secondary_capabilities": ["analysis", "research"],
        "speed": "fast",
        "tier_access": "free",
    },
    "qwen": {
        "provider": "groq",
        "tiers": {
            "economy":  {"model_id": "qwen/qwen3.6-27b", "tracking_key": "qwen-general"},
            "balanced": {"model_id": "qwen/qwen3.6-27b", "tracking_key": "qwen-general"},
            "quality":  {"model_id": "qwen/qwen3.6-27b", "tracking_key": "qwen-general"},
        },
        "scores": {"coding": 90, "reasoning": 88, "math": 92, "research": 70,
                   "writing": 75, "analysis": 85, "architecture": 80, "general": 82},
        "primary_capabilities": ["coding", "math", "reasoning"],
        "secondary_capabilities": ["analysis", "architecture"],
        "speed": "fast",
        "tier_access": "free",
    },
    "gpt-oss": {
        "provider": "groq",
        "tiers": {
            "economy":  {"model_id": "openai/gpt-oss-20b",  "tracking_key": "gpt-oss-20b"},
            "balanced": {"model_id": "openai/gpt-oss-120b", "tracking_key": "gpt-oss-120b"},
            "quality":  {"model_id": "openai/gpt-oss-120b", "tracking_key": "gpt-oss-120b"},
        },
        "scores": {"coding": 88, "reasoning": 90, "math": 85, "research": 75,
                   "writing": 85, "analysis": 87, "architecture": 82, "general": 88},
        "primary_capabilities": ["reasoning", "analysis", "architecture"],
        "secondary_capabilities": ["coding", "general"],
        "speed": "medium",
        "tier_access": "free",
    },
    "qwen-coder": {
        "provider": "openrouter",
        "tiers": {
            "economy":  {"model_id": "qwen/qwen3-coder-flash", "tracking_key": "qwen-coder-flash"},
            "balanced": {"model_id": "qwen/qwen3-coder",       "tracking_key": "qwen-coder"},
            "quality":  {"model_id": "qwen/qwen3-coder-next",  "tracking_key": "qwen-coder-next"},
        },
        "scores": {"coding": 99, "reasoning": 92, "math": 95, "analysis": 90,
                   "architecture": 97, "writing": 72, "research": 65, "general": 82},
        "primary_capabilities": ["coding", "architecture"],
        "secondary_capabilities": ["reasoning", "analysis", "math"],
        "speed": "medium",
        "tier_access": "free",
    },
    "gemini": {
        "provider": "google",
        "tiers": {
            "economy":  {"model_id": "gemini-3.1-flash-lite", "tracking_key": "gemini-flash-lite"},
            "balanced": {"model_id": "gemini-3-flash-preview", "tracking_key": "gemini-3-flash"},
            "quality":  {"model_id": "gemini-3.5-flash",       "tracking_key": "gemini-flash"},
        },
        "scores": {"coding": 80, "reasoning": 86, "math": 78, "research": 95,
                   "writing": 82, "analysis": 90, "architecture": 78, "general": 90},
        "primary_capabilities": ["research", "analysis", "general"],
        "secondary_capabilities": ["writing", "reasoning", "architecture"],
        "speed": "medium",
        "tier_access": "free",
        "supports_search": True,
    },
    "deepseek": {
        "provider": "openrouter",
        "tiers": {
            "economy":  {"model_id": "deepseek/deepseek-v4-flash", "tracking_key": "deepseek-flash"},
            "balanced": {"model_id": "deepseek/deepseek-v4-flash", "tracking_key": "deepseek-flash"},
            "quality":  {"model_id": "deepseek/deepseek-v4-flash", "tracking_key": "deepseek-flash"},
        },
        "scores": {"coding": 95, "reasoning": 98, "math": 97, "research": 78,
                   "writing": 85, "analysis": 97, "architecture": 94, "general": 90},
        "primary_capabilities": ["reasoning", "coding", "math"],
        "secondary_capabilities": ["analysis", "architecture", "general"],
        "speed": "fast",
        "tier_access": "free",
    },
}

IMAGE_FAMILIES: dict = {
    "flux": {
        "provider": "cloudflare",
        "description": "Free fast image generation.",
        "supports_consensus": False,
        "supports_multimodal": True,
        "supports_search": False,
        "tiers": {
            "economy":  {"model_id": "@cf/black-forest-labs/flux-1-schnell", "tracking_key": "flux-schnell"},
            "balanced": {"model_id": "@cf/black-forest-labs/flux-1-schnell", "tracking_key": "flux-schnell"},
            "quality":  {"model_id": "@cf/black-forest-labs/flux-1-schnell", "tracking_key": "flux-schnell"},
        },
        "tier_access": "free",
    },
}

FAMILY_FALLBACKS: dict[str, str | None] = {
    "qwen":       "deepseek",
    "gpt-oss":    "deepseek",
    "llama":      "deepseek",
    "qwen-coder": "gpt-oss",
    "deepseek":   "gpt-oss",
    "gemini":     None,
}

MULTI_AGENT_CAPABILITY_MAP: dict[str, dict[str, list[str]]] = {
    "coding":        {"economy": ["qwen-coder", "qwen", "gpt-oss"],
                       "balanced": ["qwen-coder", "deepseek", "gpt-oss"],
                       "quality": ["qwen-coder", "deepseek", "gpt-oss"]},
    "architecture":  {"economy": ["gpt-oss", "qwen", "qwen-coder"],
                       "balanced": ["gpt-oss", "qwen-coder", "deepseek"],
                       "quality": ["gpt-oss", "deepseek", "qwen-coder"]},
    "reasoning":     {"economy": ["gpt-oss", "qwen", "deepseek"],
                       "balanced": ["deepseek", "gpt-oss", "qwen"],
                       "quality": ["deepseek", "gpt-oss", "qwen-coder"]},
    "math":          {"economy": ["qwen", "gpt-oss", "deepseek"],
                       "balanced": ["qwen", "deepseek", "gpt-oss"],
                       "quality": ["deepseek", "qwen", "qwen-coder"]},
    "research":      {"economy": ["gemini", "gpt-oss", "qwen"],
                       "balanced": ["gemini", "gpt-oss", "deepseek"],
                       "quality": ["gemini", "deepseek", "gpt-oss"]},
    "writing":       {"economy": ["gemini", "llama", "gpt-oss"],
                       "balanced": ["gemini", "gpt-oss", "llama"],
                       "quality": ["gemini", "gpt-oss", "deepseek"]},
    "analysis":      {"economy": ["gemini", "gpt-oss", "qwen"],
                       "balanced": ["gemini", "gpt-oss", "deepseek"],
                       "quality": ["gemini", "deepseek", "gpt-oss"]},
    "general":       {"economy": ["gemini", "llama", "gpt-oss"],
                       "balanced": ["gemini", "gpt-oss", "qwen"],
                       "quality": ["gemini", "gpt-oss", "deepseek"]},
}

MODEL_COSTS = {
    "gpt-oss":    {"economy": 1, "balanced": 2, "quality": 2},
    "qwen":       {"economy": 2, "balanced": 2, "quality": 2},
    "gemini":     {"economy": 1, "balanced": 2, "quality": 3},
    "llama":      {"economy": 1, "balanced": 2, "quality": 3},
    "deepseek":   {"economy": 1, "balanced": 1, "quality": 2},
    "qwen-coder": {"economy": 1, "balanced": 2, "quality": 3},
}

MODEL_BENCHMARKS = {
    "qwen":       {"coding": 96, "reasoning": 94, "math": 95},
    "gemini":     {"research": 99, "analysis": 97},
    "gpt-oss":    {"coding": 95, "reasoning": 95},
    "deepseek":   {"coding": 96, "reasoning": 98, "math": 98, "analysis": 97},
    "qwen-coder": {"coding": 99, "architecture": 97},
}

EXECUTION_MODES = ["single", "consensus", "multi_agent"]

CONSENSUS_STRATEGIES = {
    "coding":       [["qwen-coder", "gpt-oss"], ["qwen-coder", "deepseek"], ["qwen", "gpt-oss"]],
    "research":     [["gemini", "gpt-oss"], ["gemini", "deepseek"]],
    "analysis":     [["gemini", "gpt-oss"], ["gemini", "deepseek"]],
    "architecture": [["gpt-oss", "qwen-coder"], ["gpt-oss", "deepseek"]],
    "reasoning":    [["deepseek", "gpt-oss"], ["deepseek", "qwen"], ["gpt-oss", "qwen"]],
    "math":         [["deepseek", "qwen"], ["qwen", "gpt-oss"]],
}

FUTURE_FEATURES = {"planner": False, "multi_agent": True}

# Same hook as backend's catalog — stays empty until you add high-level
# (paid) models. The moment you populate this, get_multi_agent_families(),
# pick_family(), and the consensus/multi-agent execution paths all pick
# them up automatically for any tier_access="premium" request — nothing
# else in orchestrator.py, router_v2.py, consensus_v2.py, or
# multi_agent.py needs to change.
PREMIUM_FAMILIES: dict = {}
PREMIUM_MODEL_COSTS: dict = {}
PREMIUM_MODEL_BENCHMARKS: dict = {}

BRAIN_PROVIDER = "groq"
BRAIN_MODEL_ID = "openai/gpt-oss-20b"


def _build_model_status() -> dict:
    status = {}
    for family in FAMILIES.values():
        for tier in family["tiers"].values():
            status[tier["tracking_key"]] = {"healthy": True, "last_check": None, "reason": None}
    for family in IMAGE_FAMILIES.values():
        for tier in family["tiers"].values():
            status[tier["tracking_key"]] = {"healthy": True, "last_check": None, "reason": None}
    return status


MODEL_STATUS = _build_model_status()


def get_model_id(family_key: str, tier: QualityTier) -> str:
    if family_key in FAMILIES:
        return FAMILIES[family_key]["tiers"][tier]["model_id"]
    if family_key in IMAGE_FAMILIES:
        return IMAGE_FAMILIES[family_key]["tiers"][tier]["model_id"]
    if family_key in PREMIUM_FAMILIES:
        return PREMIUM_FAMILIES[family_key]["tiers"][tier]["model_id"]
    raise KeyError(f"Unknown family: {family_key}")


def get_provider(family_key: str) -> str:
    for table in (FAMILIES, IMAGE_FAMILIES, PREMIUM_FAMILIES):
        if family_key in table:
            return table[family_key]["provider"]
    raise KeyError(f"Unknown family: {family_key}")


def get_scores(family_key: str) -> dict:
    if family_key in FAMILIES:
        return FAMILIES[family_key]["scores"]
    return PREMIUM_FAMILIES.get(family_key, {}).get("scores", {})


def get_benchmarks(family_key: str) -> dict:
    return MODEL_BENCHMARKS.get(family_key, PREMIUM_MODEL_BENCHMARKS.get(family_key, {}))


def get_cost(family_key: str, tier: QualityTier) -> int:
    return MODEL_COSTS.get(family_key, PREMIUM_MODEL_COSTS.get(family_key, {})).get(tier, 3)


def get_consensus_strategy(capability: str) -> list[list[str]]:
    return CONSENSUS_STRATEGIES.get(capability, [])


def get_family_fallback(family_key: str) -> str | None:
    return FAMILY_FALLBACKS.get(family_key)


def get_multi_agent_families(capability: str, quality_mode: QualityTier) -> list[str]:
    return MULTI_AGENT_CAPABILITY_MAP.get(capability, {}).get(quality_mode, [])


def all_family_keys(tier_access: str = "free") -> list:
    pool = {**FAMILIES, **PREMIUM_FAMILIES}
    return [k for k, v in pool.items() if v.get("tier_access") == tier_access]


def get_tracking_key(family_key: str, tier: QualityTier) -> str:
    if family_key in FAMILIES:
        return FAMILIES[family_key]["tiers"][tier]["tracking_key"]
    if family_key in IMAGE_FAMILIES:
        return IMAGE_FAMILIES[family_key]["tiers"][tier]["tracking_key"]
    if family_key in PREMIUM_FAMILIES:
        return PREMIUM_FAMILIES[family_key]["tiers"][tier]["tracking_key"]
    raise KeyError(f"Unknown family: {family_key}")