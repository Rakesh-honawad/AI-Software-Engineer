# API v1 (updated — see background_indexing_v1.md and dead_code_analysis_v1.md for what changed)

Source of truth is the code in `api/main.py`, `api/routes/`,
`api/schemas/`, `api/dependencies/`.

## What this is

The REST surface `backend/` (and `workspace-ui/`) actually call — the
first place everything built so far becomes reachable over HTTP
instead of only callable from a Python test script.

## Endpoints

```
GET  /health
POST /repositories/index               {repository_id, local_path OR (github_owner + github_repo + github_token?)}
                                        Returns 202 (NOT 200) — see "Background indexing" below.
GET  /repositories/{id}                status — poll after POST /index until index_status is Ready/Failed
GET  /repositories/{id}/graph          raw nodes/edges — what workspace-ui's canvas renders
GET  /repositories/{id}/profile
POST /repositories/{id}/impact         {symbol_name, node_type?} — structured only, no AI
POST /repositories/{id}/impact/narrate {symbol_name, node_type?} — same + plain-English via the router
GET  /repositories/{id}/dead-code                    structured only, no AI — see dead_code_analysis_v1.md
POST /repositories/{id}/dead-code/explain {node_id}  reads real source + AI reasoning for ONE finding
```

## Background indexing (changed — see background_indexing_v1.md for the full story)

`POST /repositories/index` used to block the HTTP request for the
entire pipeline (clone/scan → parse → build graph → Tier 2 backfill).
It now returns **`202`** almost immediately, with
`index_status: "Indexing"`, and does the real work via FastAPI
`BackgroundTasks` after the response is sent. Callers MUST poll
`GET /repositories/{id}` until `index_status` is `"Ready"` or
`"Failed"` before assuming the repo is usable — a `/graph` or
`/dead-code` call against a still-indexing repo will 404, not because
anything is broken, but because indexing genuinely isn't done yet.

**Trade-off this introduces**: errors that only surface INSIDE the
pipeline (e.g. malformed source causing a parse error) can no longer
become a synchronous `400` — they land as `index_status: "Failed"` +
the new `error_message` field on `RepositoryStatusResponse`,
discoverable only by polling. The route still validates `local_path`
existing on disk synchronously (`400` immediately) since that's the
most common bad-request case and cheap to check up front.

## Why schemas are separate Pydantic models, not the internal dataclasses

Explicit earlier decision: API contract and internal storage shape
change for different reasons and on different schedules. A field can
be renamed in `RepositoryProfile` without breaking API consumers, and
vice versa, only because `api/schemas/*.py` and
`storage/repository_profile/types.py` are two different classes.
`github_token` is also `repr=False` in its schema so it's never
accidentally echoed back in a response or log.

## The registry gap this exposed

Every module up to this point took a `storage_dir` the caller already
knew. An API can't work that way — `GET /repositories/{id}` only has
an id. Added `repository/manager/registry.py`: a small
`repository_id -> storage_dir` JSON-file lookup, auto-registered on
every successful `index_repository()` call. Since the move to
background indexing, this registration now happens inside
`begin_indexing()` immediately (not after the pipeline completes) —
so `GET /repositories/{id}` can find and poll a repo while it's still
indexing, not just after it's done.

## Error translation

Every internal exception gets translated to a specific HTTP status,
not left to bubble up as a generic 500:

| Internal exception | HTTP status |
|---|---|
| `SymbolNotFoundError` | 404 |
| `AmbiguousSymbolError` | 409, with every candidate in the response body |
| repository not registered | 404 |
| `RepositoryLockedError` | 409 |
| `GitHubRepoNotFoundError` | 404 |
| `GitHubRateLimitError` | 429 |
| `GitCloneError` | 502 |
| `AllProvidersFailedError` (narrate / dead-code explain routes) | 503 — structured analysis still succeeded, only narration/explanation failed |
| malformed index request (neither/both source options given) | 400 |
| `local_path` doesn't exist | 400 |
| dead-code explain: `node_id` not a CURRENT finding | 404 |

## Verified end to end (real backend, real repo — `repo:miniai-backend`, not TestClient mocks for the newer endpoints)

- Health check, index-by-local-path (now confirmed returning 202 +
  Indexing immediately, then polled to Ready), status, profile, graph
  endpoint — all confirmed against real data.
- Dead-code endpoints confirmed live: `GET .../dead-code` returned 70
  real findings against 154 real nodes; `POST .../dead-code/explain`
  confirmed executing its full pipeline (repo lookup, finding match,
  real source read, prompt build, router call) up through a correct
  `503` when no AI provider was configured.
- Impact analysis through the API matches direct module-level testing
  exactly (from earlier TestClient-based verification, unchanged).
- Every error path in the table above verified to return its correct
  status and a clear message, including the two new 400/404 cases
  added by background indexing and dead-code explain.

**Not tested:** GitHub-based indexing through this specific route —
skipped to avoid further consuming the sandbox's shared GitHub API
rate limit (unchanged from original testing). AI explain/narrate
responses have never been verified against a LIVE model — every test
so far correctly exercises the 503 fallback path, not a real
response, since no provider has been connected yet (deliberate).

## Current status

- ✅ Built and verified as above, including the newer background
  indexing and dead-code endpoints, all against real data.
- ❌ Still no auth/authz on any endpoint — anyone who can reach this
  API can index or query any repository. Fine for local MVP use, a
  real gap before any multi-user or hosted deployment.
- ❌ No `config/settings.py` yet — `STORAGE_ROOT` in
  `routes/repositories.py` is currently a hardcoded system temp path,
  should move to real config once that module exists.
- ✅ ~~`POST /repositories/index` blocks the HTTP request for the
  entire indexing pipeline~~ — CLOSED, see background_indexing_v1.md.
  Frontend's polling loop to actually consume the new `202` contract
  is the remaining piece (see workspace_ui_v1.md's current-status
  list) — the API itself is done.