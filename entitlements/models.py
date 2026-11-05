"""
models.py

The one authoritative record per user. Every field that matters for a
security decision is set by the SERVER, never accepted from the client.
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum

from entitlements.config import Plan


class AccessStatus(str, Enum):
    TRIAL_ACTIVE = "trial_active"
    TRIAL_EXPIRED = "trial_expired"
    SUBSCRIBED = "subscribed"
    SUBSCRIPTION_EXPIRED = "subscription_expired"
    NO_RECORD = "no_record"          # user has never started a trial or subscribed


@dataclass
class EntitlementRecord:
    user_id: str                     # Supabase auth user id — the only real key
    plan: Plan
    trial_started_at: datetime | None
    trial_ends_at: datetime | None
    subscription_started_at: datetime | None
    subscription_ends_at: datetime | None
    secondary_signal_hash: str | None = None   # optional device/email hash, see security.py
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def status(self, now: datetime | None = None) -> AccessStatus:
        now = now or datetime.now(timezone.utc)

        if self.plan in (Plan.SUB_MEDIUM, Plan.SUB_TOP):
            if self.subscription_ends_at and now > self.subscription_ends_at:
                return AccessStatus.SUBSCRIPTION_EXPIRED
            return AccessStatus.SUBSCRIBED

        if self.plan == Plan.TRIAL:
            if self.trial_ends_at and now > self.trial_ends_at:
                return AccessStatus.TRIAL_EXPIRED
            return AccessStatus.TRIAL_ACTIVE

        return AccessStatus.TRIAL_EXPIRED  # Plan.EXPIRED terminal state