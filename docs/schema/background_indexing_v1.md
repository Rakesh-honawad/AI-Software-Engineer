# Background indexing v1

Source of truth is the code in `repository/manager/repository_manager.py`
and `api/routes/repositories.py`.

## What this is

Closes the known gap documented in `api_v1.md`: `POST
/repositories/index` used to block the HTTP request for the entire
pipeline duration (clone/scan → parse → build graph → Tier 2 backfill)
— fine for a small repo, a real problem for a large one. The pipeline
now runs after the response is already sent, using FastAPI's built-in
`BackgroundTasks` — no new infrastructure (no Celery/Redis), same
"start simple" pattern used everywhere else in this project. `jobs/`
remains a Phase 2 stub; this is deliberately the simple version of
that eventual real job queue, not a replacement for it.

## The split

`repository_manager.py`'s old single `index_repository()` function is
now two functions plus a thin wrapper:

- **`begin_indexing(repo_path, repository_id, storage_dir) ->
  RepositoryRecord`** — FAST, synchronous. Acquires the file lock,
  creates/loads the `RepositoryRecord`, registers `repository_id ->
  storage_dir` immediately (so `GET /repositories/{id}` can find and
  poll it right away, not just after completion), sets status to
  `INDEXING`, returns. Raises `RepositoryLockedError` immediately if a
  run is already in progress — this must surface synchronously to the
  HTTP caller, not silently fail inside a background task where
  nothing could ever report it.

- **`run_indexing(repo_path, repository_id, storage_dir) -> None`** —
  SLOW. The actual parse/build/save/backfill work (unchanged from the
  old single-function version). Updates the record to `READY` or
  `FAILED` when done, releases the lock in a `finally` block. Handed
  to FastAPI's `BackgroundTasks` by the route; has no FastAPI
  dependency itself, so it's equally callable directly from a script
  for the old fully-synchronous behavior.

- **`index_repository(...)`** — kept as a synchronous convenience
  wrapper (`begin_indexing` then immediately `run_indexing`,
  blocking). Every test/script written against the old signature keeps
  working unchanged. The API route does NOT use this wrapper — it
  calls `begin_indexing`/`run_indexing` directly so it can return
  before `run_indexing` finishes.

## The real trade-off this introduces

Before, a bad repo (e.g. malformed code causing
`generate_tier1_profile()` to raise `ValueError`) came back as an
immediate `400`. **It can't anymore** — the response is already sent
by the time that error happens inside the background task. It instead
surfaces as `index_status: "Failed"` + a new `error_message` field on
`RepositoryStatusResponse`, discoverable only by polling
`GET /repositories/{id}` afterward.

The route still validates what it reasonably can up front — `local_path`
actually existing on disk is checked synchronously, still a `400` —
so the single most common "obviously bad request" case still fails
fast. Anything deeper in the pipeline is poll-only now.

## API contract change

`POST /repositories/index` now returns **`202`** (was `200`), with
`index_status: "Indexing"` — not `"Ready"`. Callers must poll
`GET /repositories/{id}` until `index_status` is `"Ready"` (done) or
`"Failed"` (check `error_message`) before assuming the repo is usable.

**Frontend implication, not yet built**: `workspace-ui`'s graph-loading
hook currently does a single `GET .../graph` fetch and shows an error
on 404 — with background indexing, a freshly-triggered index will
legitimately 404 on `/graph` for a while by design. A polling loop
against `GET /repositories/{id}` that waits for `"Ready"` before
hitting `/graph` is the correct fix, not yet implemented as of this
doc.

## Verified live

- Confirmed via real `Invoke-RestMethod` call against
  `repo:miniai-backend`: `POST /index` returned in well under a
  second with `index_status: "Indexing"` (previously would have
  blocked until the whole pipeline finished).
- Confirmed polling `GET /repositories/{id}` correctly transitioned
  `Indexing -> Ready` on a subsequent call.
- Confirmed the underlying pipeline itself still produces correct
  results under the new split — same graph, same profile, same
  `entry_points` fix reflected, as the old synchronous path did.

## Current status

- ✅ Split implemented, live-verified against a real repo.
- ✅ `error_message` field added to `RepositoryStatusResponse`.
- ❌ Frontend polling loop for the new `202`/poll-until-ready contract
  — not built. `workspace-ui` still assumes a `/graph` 404 means
  "not indexed" rather than "indexing in progress," which will show a
  misleading error during the (usually short) indexing window.
- ❌ Real job queue (Celery/Redis or similar) — still not needed at
  current scale; `jobs/` remains an empty Phase 2 stub.