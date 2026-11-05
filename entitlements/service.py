"""
service.py

The only entrypoint the rest of knowledge-engine/backend should import.
Everything above this file (store, security, config, models) is an
implementation detail.
"""

from datetime import datetime, timezone

from entitlements.config import CONFIG, Plan, PLAN_TO_ROUTER_TIER
from entitlements.models import EntitlementRecord, AccessStatus
from entitlements import store


class AccessDeniedError(Exception):
    def __init__(self, status: AccessStatus, message: str):
        self.status = status
        self.message = message
        super().__init__(message)


def _record_from_row(row: dict) -> EntitlementRecord:
    def parse(v):
        return datetime.fromisoformat(v) if v else None

    return EntitlementRecord(
        user_id=row["user_id"],
        plan=Plan(row["plan"]),
        trial_started_at=parse(row["trial_started_at"]),
        trial_ends_at=parse(row["trial_ends_at"]),
        subscription_started_at=parse(row["subscription_started_at"]),
        subscription_ends_at=parse(row["subscription_ends_at"]),
        secondary_signal_hash=row["secondary_signal_hash"],
        created_at=parse(row["created_at"]),
        updated_at=parse(row["updated_at"]),
    )


def get_status(user_id: str) -> AccessStatus:
    row = store.get_record(user_id)
    if row is None:
        return AccessStatus.NO_RECORD
    return _record_from_row(row).status()


def start_trial_if_new(user_id: str, secondary_signal_raw: str | None = None) -> AccessStatus:
    """
    Idempotent: call this the first time a user opens Engineer Mode. If
    they already have a record (trial or subscription, active or expired),
    nothing changes — the existing status is simply returned. A brand-new
    user gets exactly one trial, ever, enforced at the DB layer.
    """
    secondary_hash = None
    if CONFIG.require_secondary_trial_check and secondary_signal_raw:
        from security import hash_secondary_signal
        secondary_hash = hash_secondary_signal(secondary_signal_raw)
        if store.has_secondary_signal_been_used(secondary_hash):
            # Someone's reused a device/email signal under a new account —
            # don't grant a second trial, but don't hard-fail either;
            # fall through and let the normal insert-if-new logic apply
            # (it will simply not create a NEW trial for this identity,
            # but existing users/subscribers are unaffected).
            pass

    store.insert_trial_if_new(user_id, CONFIG.trial_days, secondary_hash)
    return get_status(user_id)


def check_access(user_id: str) -> str:
    """
    Raises AccessDeniedError if the user shouldn't get into Engineer Mode
    right now. Returns the router ServiceTier string (e.g. "free",
    "startup_medium") to use for this request if access is granted.

    Call this on every Engineer Mode request that touches the AI router or
    any gated feature — never cache the result client-side as the access
    decision itself.
    """
    if CONFIG.dev_bypass:
        return "free"

    row = store.get_record(user_id)
    if row is None:
        raise AccessDeniedError(
            AccessStatus.NO_RECORD,
            "No trial started yet — call start_trial_if_new first."
        )

    record = _record_from_row(row)
    status = record.status()

    if status == AccessStatus.TRIAL_EXPIRED:
        raise AccessDeniedError(status, "Your 7-day free trial has ended. Please subscribe to continue.")
    if status == AccessStatus.SUBSCRIPTION_EXPIRED:
        raise AccessDeniedError(status, "Your subscription has expired. Please renew to continue.")

    tier = PLAN_TO_ROUTER_TIER[record.plan]
    if tier is None:
        raise AccessDeniedError(status, "No active plan grants access.")
    return tier