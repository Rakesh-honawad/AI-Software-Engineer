"""
config.py

Every knob you'd want to change based on "the situation" lives here and
nowhere else. Nothing downstream hardcodes a number of days or a plan name.
"""

import os
from dataclasses import dataclass
from enum import Enum


class Plan(str, Enum):
    TRIAL = "trial"                  # one-time free window
    EXPIRED = "expired"              # trial used up, not subscribed
    SUB_MEDIUM = "sub_medium"        # paid — maps to ServiceTier.STARTUP_MEDIUM
    SUB_TOP = "sub_top"              # paid — maps to ServiceTier.STARTUP_TOP


@dataclass(frozen=True)
class EntitlementConfig:
    # How long the one-time free trial lasts. Change via env var so you can
    # adjust per campaign without a redeploy — e.g. TRIAL_DAYS=14 for a promo.
    trial_days: int = int(os.getenv("ENGINEER_MODE_TRIAL_DAYS", "7"))

    # If True, a second signal (email domain / device hash — see security.py)
    # is also checked before granting a trial, to slow down "just make a new
    # account" abuse. Off by default; flip on if you see abuse.
    require_secondary_trial_check: bool = os.getenv(
        "ENGINEER_MODE_TRIAL_SECONDARY_CHECK", "false"
    ).lower() == "true"

    # Grace period after trial/subscription expiry where access is blocked
    # but the record isn't purged (lets you comp people manually if needed).
    grace_hours: int = int(os.getenv("ENGINEER_MODE_GRACE_HOURS", "0"))

    # Temporary local development bypass. Disabled by default.
    dev_bypass: bool = os.getenv(
        "ENGINEER_MODE_DEV_BYPASS", "false"
    ).lower() == "true"


CONFIG = EntitlementConfig()

# Which model-router ServiceTier each plan is allowed to use. This is the
# ONLY place that connects entitlements to ai/router/config.py's tiers —
# change a mapping here, nothing else needs to know.
PLAN_TO_ROUTER_TIER = {
    Plan.TRIAL: "free",
    Plan.EXPIRED: None,          # None = no access, caller must block before routing
    Plan.SUB_MEDIUM: "startup_medium",
    Plan.SUB_TOP: "startup_top",
}