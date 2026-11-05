# Dead-code analysis v1

Source of truth is the code in `graph/algorithms/dependency/reachability.py`,
`analysis/dead_code/dead_code_analysis.py`, `contracts/analysis/dead_code_result.py`,
`ai/narration/dead_code_narration.py`, and `api/routes/dead_code.py`.

## What this is

Answers "what in this repo does nothing reach from a known entry
point" — a second flagship analysis alongside impact analysis, built
on the same graph. Two layers, same separation-of-concerns pattern as
impact analysis:

```
entry point file paths (from RepositoryProfile.entry_points)
    ↓  graph/algorithms/dependency/reachability.py   (pure graph theory)
{reached node ids}  — forward BFS from every root simultaneously
    ↓  analysis/dead_code/dead_code_analysis.py        (domain interpretation, NO AI)
DeadCodeAnalysisResult  — structured, contracts/analysis/
    ↓  ai/narration/dead_code_narration.py              (OPTIONAL, per-finding, NOT auto-run)
plain-English reasoning for one specific finding, via POST .../dead-code/explain
```

**Why this needed a whole extra detection layer that impact analysis
didn't:** impact analysis always has an explicit starting point (the
node the developer says they're changing). Dead-code analysis has to
figure out its own starting points — "what counts as externally
invoked" — with nothing handed to it. That's the hard, honest part of
this feature; see "Entry-point detection" below.

## Layer 1 — `graph/algorithms/dependency/reachability.py`

`forward_reachability(root_ids, edges, edge_types=DEFAULT_REACHABILITY_EDGE_TYPES) -> set[str]`

Forward BFS from every id in `root_ids` simultaneously, along `calls`/
`inherits`/`implements` edges only. Mirrors `impact/traversal.py`'s
shape exactly, just inverted (forward instead of reverse, multiple
roots instead of one changed node). No domain interpretation — same
"only answers what's reachable" boundary as the impact traversal.
Reusable beyond dead-code analysis (e.g. a future real feature-tracing
analysis could use this directly).

## Layer 2 — `analysis/dead_code/dead_code_analysis.py`

`analyze_dead_code(repository_id, nodes, edges, entry_point_file_paths) -> DeadCodeAnalysisResult`

**Candidate types** (eligible to be flagged dead): `Function`, `Class`,
`Service`, `RepositoryLayer`. Deliberately excludes `Module`/`File`/
`Repository` (structural containers), `Configuration`/`Entity`/
`ExternalDependency` (not executable code), `Interface` (an unused
interface is a different finding), `Feature` (AI-synthesized
grouping), `Test` (an unrun test is a different concern than dead
production code).

### Entry-point detection — the real complexity here, read before trusting output

The current graph builder only produces `File`/`Class`/`Function`
nodes and `contains`/`calls`/`inherits` edges (per
`graph_builder_pipeline_v1.md`). Route decorators (`@app.get(...)`)
are **not parsed into the graph** — a route handler function
genuinely has zero incoming `calls` edges, indistinguishable from
truly dead code at the graph level. Root detection mitigates this,
does not solve it:

1. A candidate node is a root if its `file_path` matches one of
   `RepositoryProfile.entry_points`.
2. A candidate node is a root if its name matches a small conventional
   allowlist (`main`, `__main__`, `run`).
3. **Defensive fallback**, independent of #1: if a candidate's file
   basename is `main.py`/`app.py`/`asgi.py`/`wsgi.py`/`manage.py`/
   `server.py`/etc., its whole file is treated as a root regardless of
   whether `entry_points` caught it. Added after a real bug where
   `entry_points` was empty for a perfectly normal FastAPI repo (see
   `repository_profile_schema_v1.md`'s entry-point detection notes) —
   this analysis doesn't fully trust upstream data will always be
   complete.

This intentionally does **not** try to detect API routes by name
alone — that only sets `possible_entry_point` on a finding (a
heuristic hint for the reviewer), never grants root status by itself.

### `DeadCodeAnalysisResult.caveat`

**Always populated on every result, not just available on request.**
States the decorator-blind-spot limitation in plain language. Any UI
surfacing findings must surface this alongside them, every time — see
`workspace_ui_v1.md`'s dead-code panel, which renders it as a
persistent, non-dismissible banner above the findings list.

### Per-finding fields

`node_id`, `node_type`, `name`, `file_path`, `possible_entry_point`
(heuristic — name matches common route/handler naming patterns like
`get_*`/`*_handler`), `fan_in_from_other_dead_code` (raw incoming
`calls` count regardless of whether the caller is itself reachable —
a nonzero value here means "dead code calling dead code," a mutually
isolated cluster, not a traversal bug).

Findings are sorted `possible_entry_point` last, so the
highest-confidence findings surface first when reviewing top to bottom.

## Layer 3 — `ai/narration/dead_code_narration.py` (optional, per-finding)

**Not auto-run for every finding** — the AI panel's honest-scope
pattern applies here too. A developer explicitly requests an
explanation for ONE finding at a time via the workspace-ui's "Explain"
button, which calls `POST /repositories/{id}/dead-code/explain`.

**Why this reads real source code, not just the structural fact:** a
"zero incoming calls" verdict alone isn't enough for a developer to
safely delete something on. This module builds a prompt that includes
the finding's actual source text (capped at `MAX_SOURCE_CHARS=6000`)
plus the structural finding and the caveat, and asks the model to
reason specifically about decorators, dependency injection (e.g.
FastAPI's `Depends()`), dynamic dispatch, and any docstring/comment
signal that the code is planned-but-unfinished rather than abandoned.

**Hard rule, same as `impact_narration.py`'s "explain and hint only":**
never recommends deletion — only gives a reasoned read (looks-dead /
looks-live / unsure) with reasoning and any future-feature signal
found. Enforced by prompt wording only, not a code-level guard — same
documented limitation as impact narration's equivalent rule.

Same injected-`llm_call` pattern as `impact_narration.py` — doesn't
import `ai/router/` directly.

## API — `api/routes/dead_code.py`

```
GET  /repositories/{id}/dead-code                structured only, no AI, always fast
POST /repositories/{id}/dead-code/explain  {node_id}   reads real source + AI reasoning for ONE finding
```

`GET .../dead-code` re-derives the full analysis on every call (does
not cache) — cheap enough at current repo sizes; revisit if this ever
needs to run against a very large indexed repo repeatedly.

`POST .../dead-code/explain` re-runs `analyze_dead_code` internally to
confirm the requested `node_id` is a CURRENT finding (404s if not —
e.g. if the repo was reindexed and that node no longer qualifies),
then reads the real file from `RepositoryRecord.local_path` on disk.
Can 503 if every `ai/router/` provider is unavailable — same pattern
as `/impact/narrate` (structural analysis, i.e. the finding itself,
is unaffected; only the explanation is missing).

## Verified live (real backend, real repo — `repo:miniai-backend`)

- Found the real entry-point-detection bug live: `entry_points: []`
  produced `total_reachable: 0` / 124-of-124 falsely flagged dead —
  fixed at the source (`tier1_generator.py`, see
  `repository_profile_schema_v1.md`), confirmed after fix:
  `total_reachable: 54`, 70 real findings.
- `possible_entry_point` heuristic confirmed catching real FastAPI
  dependency-injection functions (`get_current_user_id`,
  `get_user_by_id`) that the graph genuinely can't see as "used."
- `/dead-code/explain` confirmed executing its full pipeline correctly
  end-to-end (repo lookup → re-derive findings → match node_id → read
  real source → build prompt → call router) up to the router itself —
  correctly returned a `503` with `AllProvidersFailedError` since no
  AI provider was configured yet (deliberately deferred).

## Current status

- ✅ Reachability traversal, domain analysis, contract, API routes —
  built and live-verified against a real repo.
- ✅ Entry-point detection — broadened and live-fixed (was a narrow
  hardcoded guess-list, now static candidates + app-instantiation
  content scan + route-decorator-directory content scan).
- ✅ AI explain endpoint — built, pipeline verified end-to-end up to
  the router call itself.
- ❌ **Never verified against a live AI response** — no provider
  configured yet (same gap as `model_router_v1.md`'s own status,
  deliberately deferred by the user as of this writing).
- ❌ No caching — `GET .../dead-code` re-derives the full analysis
  every call. Fine at current scale.
- ❌ Route-decorator parsing itself (the actual fix to the underlying
  blind spot, vs. the current directory/naming mitigations) — not
  built. Would require `frameworks/`-level decorator awareness in the
  parser, still a Phase 2 stub project-wide.