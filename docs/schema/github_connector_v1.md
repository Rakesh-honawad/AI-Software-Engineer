# GitHub connector v1

Source of truth is the code in `ingestion/connectors/base_connector.py`
and `ingestion/connectors/github.py`.

## What this is

Turns a GitHub `owner/repo` into a local folder that
`repository_manager.index_repository()` can run against exactly like
any other local path. Nothing downstream (Tier 1, graph builder, Tier
2) knows or needs to know a repo came from GitHub instead of an
existing local clone — that boundary is the whole point of
`base_connector.py`'s `ConnectorFetchResult` contract.

GitLab/Bitbucket were explicitly deferred earlier ("GitHub alone is
enough for MVP") — either would be a new file matching the same
contract, not a change to anything that calls a connector.

## `fetch(target_dir) -> ConnectorFetchResult`

1. Calls the GitHub API for metadata (default branch, public/private,
   canonical URL).
2. If `target_dir` already has a `.git` folder: `git fetch` + `git
   reset --hard origin/<default_branch>` — updates in place rather
   than re-cloning. This is what makes calling `index_repository()`
   again on an already-indexed GitHub repo cheap.
3. Otherwise: `git clone --branch <default_branch> [--depth 1]`.
   Shallow by default (`shallow=True`) — fast, and Tier 1/2/graph
   building only need current state, not history. Full clone is a
   constructor parameter, not a redesign, if/when a "Git history
   analysis" feature (blueprint's own future idea) gets built.
4. Returns `local_path`, `default_branch`, `commit`, `is_private`,
   `remote_url`.

## Two real bugs caught by testing against the LIVE GitHub API, not just reading the code

1. **Token-in-URL credential leak.** The first draft embedded the auth
   token directly in the clone URL. `git clone`/`git remote` writes
   the remote URL permanently into `.git/config` — meaning the token
   would sit there in plaintext for anyone who later reads that file.
   Fixed: the token is now passed as a transient
   `http.extraheader=AUTHORIZATION: bearer <token>` git config flag
   for that invocation only, never written to disk.
2. **Missing `User-Agent` header.** GitHub's API silently 403s
   requests with none. `curl` sends one by default; `urllib.request`
   doesn't. The first live test failed with a 403 that looked like a
   rate-limit issue until traced to this.

## Error handling

- `GitHubRepoNotFoundError` — 404 from the API (repo doesn't exist, OR
  is private and the token can't see it — GitHub deliberately doesn't
  distinguish these for security, so this error can't either).
- `GitHubRateLimitError` — 403 with `X-RateLimit-Remaining: 0`.
  Verified this fires correctly against the **real** API: the sandbox
  this was built in shares an egress IP across many concurrent
  sessions, and genuinely hit the 60/hour unauthenticated limit partway
  through testing — the error correctly caught it and reported an
  accurate, actionable message rather than a raw traceback.
- `GitCloneError` — the `git` subprocess itself failed; carries git's
  own stderr.

## Verified live (real API calls, real `git clone`, not mocked)

- Metadata fetch against `octocat/Hello-World`
- Full clone to disk
- Update-existing-clone path (`fetch` + `reset --hard`) — landed on the
  identical commit as the first clone
- Not-found handling against a genuinely nonexistent repo
- Rate-limit handling against a genuinely rate-limited response

## Current status

- ✅ GitHub connector — built and live-verified.
- ❌ GitLab, Bitbucket — deferred per earlier decision, not built.
- ❌ Not wired into a background job — `fetch()` blocks the caller for
  as long as the clone takes. Fine at MVP scale; `jobs/` (still a
  Phase 2 stub) is where this would move if clone time becomes a
  real UX problem.