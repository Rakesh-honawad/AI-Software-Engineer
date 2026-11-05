# entitlements/ — free trial, subscription gating, anti-abuse

## Where these go
Put this as its own top-level folder in knowledge-engine (sibling to `ai/`),
e.g. `G:\miniai\knowledge-engine\entitlements\`. Deliberately separate from
`ai/router/` — one decides "can this user use Engineer Mode at all and at
what tier," the other decides "which model answers this specific request."

## The security model, plainly
- The trial clock is set **once, server-side**, in the database — never
  from anything the client sends. `store.insert_trial_if_new()` uses a
  DB-level `INSERT ... ON CONFLICT DO NOTHING` keyed on `user_id`, so it's
  safe even under concurrent/replayed requests — impossible to restart a
  trial by clearing local storage, resending a request, or scripting calls.
- Every gated request re-checks the DB via `check_access()`. There's no
  "cache the expiry date in the browser and trust it later" path for the
  actual access decision.
- `security.py`'s signed token is a **display convenience only** (e.g. "3
  days left" badge) — even if someone forges or replays it, it grants
  nothing, because the real check ignores it and re-queries the DB.
- `ENTITLEMENT_SIGNING_SECRET` must be a real random secret in your env,
  never committed to git. Generate with:
  `python -c "import secrets; print(secrets.token_hex(32))"`

## What's easy to change, and where
| Want to change...                        | Edit only...                          |
|-------------------------------------------|----------------------------------------|
| Trial length (7 days → 14, etc.)          | `ENGINEER_MODE_TRIAL_DAYS` env var     |
| Which router tier a plan maps to          | `PLAN_TO_ROUTER_TIER` in config.py     |
| Add a new paid plan                       | Add to `Plan` enum + the mapping dict  |
| Turn on device/email abuse checking       | `ENGINEER_MODE_TRIAL_SECONDARY_CHECK=true` |
| Rate limit on trial-start endpoint        | `trial_start_limiter` args in security.py |

No code path reads a hardcoded "7" anywhere — grep confirms it only exists
as the config default.

## Wiring into your FastAPI app
```python
from entitlements.dependencies import require_engineer_mode_access, start_trial_endpoint

app.post("/entitlements/start-trial")(start_trial_endpoint)

@app.get("/repositories/{id}/dead-code/explain")
async def explain(id: str, tier: str = Depends(require_engineer_mode_access)):
    result = await run_task("dead_code_explain", ServiceTier(tier), prompt)
    ...
```
Swap the placeholder `user=Depends()` in `dependencies.py` for your real
Supabase `get_current_user` dependency — that's the only integration point
with your existing auth.

## Free tier stays basic — already enforced
Trial and free-plan users resolve to `ServiceTier.FREE` in the AI router,
which only pools `low`/`medium` quality models (see `ai/router/config.py`).
Nothing in entitlements needs to duplicate that restriction — it falls out
naturally from the tier mapping.

## Moving off SQLite later
`store.py` is plain SQL against SQLite (`entitlements.db` by default, path
configurable via `ENTITLEMENTS_DB_PATH`). To point it at your Supabase
Postgres instead: swap `sqlite3.connect(...)` for your Postgres connection,
change `?` placeholders to `%s`, and change `INSERT ... ON CONFLICT(...) DO
NOTHING` syntax stays identical (Postgres supports it natively — SQLite's
version was modeled on Postgres's). Everything above `store.py` is unaware
of which database it's talking to.

## Verified this session (sandbox test, not your real DB)
- New user → trial starts, status `trial_active`, tier resolves to `free`
- Calling start-trial again → no-op, same `trial_started_at`, not extended
- Backdating `trial_ends_at` → `check_access` correctly raises and blocks
- No record at all → correctly raises `NO_RECORD`, not silently allowed
- Subscribed user → resolves to their paid tier (`startup_top` in the test)

## Not done yet (intentionally, matches your ask to keep this minimal for now)
- Actual payment/subscription webhook (Razorpay, per your product notes) to
  call `store.upsert_subscription()` — this module only handles the
  gating/state side, not billing itself
- Admin override endpoint (comp a user, extend a trial manually)
- The secondary device/email abuse signal is wired but off by default
  (`require_secondary_trial_check=False`) — you'll need to decide what to
  hash (email, a frontend-generated device id, IP) before turning it on