# CLAUDE.md — knowledge-engine

This file is project memory. Read this before making any change here.
If you're a future Claude session picking this up cold: this file plus
`docs/schema/*.md` should be enough to continue without the user
re-explaining anything. If something here and the code disagree, the
code is right and this file is stale — fix the file.

## What this is

MiniAI is a platform with specialized AI workspaces (Developer/Engineer,
Data Analyst, Research) built on one shared AI Core. `knowledge-engine/`
is the backend for **Engineer Mode**, the first workspace being built.

**The core feature:** a developer connects a repo. Instead of an LLM
re-reading the whole codebase every time someone asks something, the
system parses it once into a persistent knowledge graph, builds a
Repository Profile, and answers "if I change this function, what
breaks?" via deterministic graph traversal + severity scoring — with
the AI only stepping in at the end to explain the result in plain
English. **The AI never replaces deterministic analysis; it explains
what the graph already determined.**

**Second flagship analysis:** "what in this repo does nothing reach from
a known entry point" — dead-code detection, same deterministic-graph-first,
AI-explains-second shape as impact analysis.

## Repo layout — READ THIS BEFORE TOUCHING FOLDERS

```
miniai/
├── backend/          existing multi-agent chat app (FastAPI). NOT YET CONNECTED to
│                     knowledge-engine — that integration is deliberately deferred.
├── frontend/         existing frontend. Unrelated to workspace-ui below.
└── knowledge-engine/ THIS project. Sibling folder, not nested in backend/.
```

`knowledge-engine/` is consumed by `backend/` as a **library** (in-process
import, e.g. `pip install -e ../knowledge-engine`), not a network service.
Do not extract this into a microservice without a real reason.

## Non-negotiable architectural rules

1. **`analysis/` never imports from `ai/`.** Ever. Analysis produces
   structured data only; narration turns that into English.
2. **AI is the final layer, not a shortcut.** It builds on top of the
   graph/profile/analysis — it doesn't replace them.
3. **Node vs. property rule**: something becomes a graph node only if
   (a) other nodes need to reference it via an edge, or (b) it needs
   its own lifecycle/query. Otherwise it's a property on an existing node.
4. **Every node and edge carries provenance**: `source` (`AST`/`Detected`/
   `AI`/`User`) and, for edges, `confidence` (0.0–1.0) and `created_by`.
5. **Start simple, keep the contract, swap the implementation later.**
   JSON-file graph storage now (target: Kuzu later), FastAPI `BackgroundTasks`
   now (target: real job queue later).
6. **Documented limitations are not bugs.** Several modules skip things
   on purpose (attribute calls like `self.foo()`, relative imports,
   cross-repo resolution) — check a module's docstring before "fixing"
   something that's actually a stated Phase 2 boundary.

## Folder map + status

| Folder | Status |
|---|---|
| `graph/schema/` | ✅ **Locked, v1.0.0.** Don't change node/edge types without a real reason — reviewed 3+ times. PascalCase wire format (`"File"`, not `"file"`). |
| `parsing/tree_sitter/`, `parsing/symbol_extractor/` | ✅ Python AND TypeScript/TSX/JavaScript/JSX. |
| `parsing/semantic/` | ✅ `attribute_call_resolver.py` — resolves `self.foo()`/`this.foo()` + inheritance chain walking. Real bug fix: reindexing user's `backend/` increased edges from 272 (proof it finds real edges). |
| `graph/builder/` | ✅ Single-file + multi-file cross-resolution. Real bug: `return result1` typo only triggered on real multi-file builds, not isolated tests. |
| `graph/algorithms/impact/` | ✅ `traversal.py` — reverse-BFS. |
| `graph/algorithms/dependency/` | ✅ `reachability.py::forward_reachability()` — forward-BFS, built for dead-code detection. |
| `graph/algorithms/{cycles,feature_tracing,shortest_path}/` | ❌ Phase 2+. |
| `analysis/impact/` | ✅ `impact_analysis.py` — severity scoring. Never imports `ai/`. |
| `analysis/dead_code/` | ✅ `dead_code_analysis.py::analyze_dead_code()` — entry-point detection + forward reachability + findings. Live-discovered-and-fixed bug: entry-point detection was returning empty for normal FastAPI layout, causing 124/124 false positives. Fixed in `tier1_generator.py::_detect_entry_points()` with static candidates + content-based scans. See `repository_profile_schema_v1_UPDATE_NOTE.md`. |
| `contracts/analysis/` | ✅ `impact_result.py` + `dead_code_result.py` (DeadCodeFinding, DeadCodeAnalysisResult — pure-data, no-computation). |
| `ai/narration/` | ✅ `impact_narration.py` + `dead_code_narration.py` — injected `llm_call` pattern, hard "never recommends deletion" rule. |
| `storage/repository_profile/` | ✅ `types.py`, `schema_version.py` (v1.1.0), `tier1_generator.py`, `tier2_backfill.py`, `profile_store.py`. Entry-point detection fixed. |
| `storage/graph/` | ✅ `json_store.py` — save/load/hash. No incremental save yet (full rewrite). |
| `ai/router/` | ✅ `providers.py` + `router.py` — live-verified (correctly raises `AllProvidersFailedError` when no provider configured, API translates to `503`). Never tested against a live model response (deliberately deferred). |
| `ai/context_builder/`, `ai/reasoning/`, `ai/prompts/`, `ai/retriever/` | ❌ Not built. Blocks general "ask anything about this repo" Q&A. |
| `ingestion/connectors/` | ✅ GitHub connector — live-tested (real API + real `git clone`). Two real bugs caught: token-in-URL credential leak (fixed to transient auth header) + missing `User-Agent` header (GitHub silently 403s without it). |
| `ingestion/{zip_handler,watcher}/` | ❌ Not built. |
| `query/` | ✅ `symbol_search.py` + `graph_query.py`. `query/text/` stubbed. |
| `repository/manager/` | ✅ **REWRITTEN by user.** Split into `begin_indexing()` (fast, synchronous: lock + register immediately) + `run_indexing()` (slow: real pipeline work). Real bug fixed: registration must happen inside `begin_indexing()`, not only on success. Windows gotcha (colon in path) handled by `_safe_folder_name()`. |
| `api/` | ✅ FastAPI app, routes, schemas. `POST /repositories/index` returns `202` with async execution (was `200`). NEW: `/dead-code` and `/dead-code/explain` endpoints. `RepositoryStatusResponse` gained `error_message` field. All live-verified against real running API. CORS middleware required and present. |
| `workspace-ui/` | ✅ **Next.js 16 + React 19 + TypeScript + Tailwind v4 + shadcn/ui.** Rebuilt from scratch externally (v0.dev), NOT by Claude in this thread. Real backend integration (adapter layer reconciles v0's mock shapes with real wire format). Dead-code UI, drag-and-drop nodes, force layout (user's version: spatial-grid O(n), better than Claude's original O(n²)). Confirmed live end-to-end: 154 nodes/272 edges on real `backend/` folder. Real bug fixed: `ApiNodeType` was lowercase in code but API returns PascalCase; fixed in `adapters.ts::toDisplayNodeType()` with case-insensitive normalization. **Honest scope note preserved in code**: "Explain this node" + "Connect to AI agent" both alias to same `/impact/narrate` call — no general Q&A endpoint yet (needs `ai/context_builder/`/`reasoning/`/`retriever/`, none built). |
| `workspace-ui-app/` | ❌ **DELETED.** Was the old Vite wrapper. `workspace-ui/` is now itself a complete Next.js app. |
| `tests/` | ❌ **All stubs** — only `__init__.py`. Verification was one-off manual API calls against live backend (genuinely found real bugs: monorepo VCS detection, `confirm_*()` dropping `user_id`, edge-counting inconsistency, Windows colon crash, registration-only-on-success bug, `result1` typo). Not persisted as regression suite. Worth prioritizing. |
| `cache/` | ⚠️ User wants this, hasn't explained design yet. Don't build without asking. |
| `jobs/` | ⚠️ Empty stub — correctly so. "Indexing blocks the HTTP request" is already solved via `repository/manager/` + FastAPI `BackgroundTasks`. Claude's original `ThreadPoolExecutor` became dead code and was deleted. Don't rebuild without confirming `BackgroundTasks` has actually become insufficient. |
| `frameworks/*`, `events/`, `telemetry/`, `lifecycle/` | ❌ Phase 2+. `frameworks/*` is what makes `Service`/`Feature`/`External dependency` real instead of decorative UI colors. |
| `repository/metadata/`, `repository/{locks,versions}/` | ❌ Not split out yet. `locks/` and `versions/` are empty stub folders; `repository_manager.py`'s own file-based locking (`.indexing.lock`) handles it. |
| `common/`, `contracts/{api,graph,repository}/`, `storage/{snapshots,summaries,embeddings}/` | ❌ Empty stubs. |

## Documentation

`docs/schema/` has one `.md` per module. **Read the relevant one before changing a module.**

Current docs:
- `graph_schema_v1.md`
- `repository_profile_schema_v1.md` + `repository_profile_schema_v1_UPDATE_NOTE.md` (entry-point fix)
- `graph_builder_pipeline_v1.md`
- `impact_analysis_v1.md`
- `dead_code_analysis_v1.md`
- `background_indexing_v1.md`
- `graph_storage_v1.md`
- `query_v1.md`
- `model_router_v1.md`
- `github_connector_v1.md`
- `api_v1.md` (UPDATED — background indexing + dead-code endpoints)
- `workspace_ui_v1.md` (describes old Claude-built Vite frontend — current real frontend is v0.dev Next.js; doc not yet rewritten)

**STILL NEEDED**: doc for `parsing/semantic/` attribute call resolver, + updated doc
for `repository/manager/` rework + new Next.js frontend. Written in this file for now;
move to `docs/schema/*.md` when there's time.

**When you finish or change a module, update its doc's "Current status" section.**
A stale doc is worse than no doc — don't let it happen.

## Conventions for new code

- **Test against the user's real repos, not just mocks.** Several real bugs were only
  caught this way (monorepo VCS detection, `confirm_*()` bugs, edge-counting issues,
  Windows path crashes, registration bugs, typos triggered only on multi-file builds).
- **Node ids are stable and structured**: `file:{path}`, `class:{path}:{ClassName}`,
  `function:{path}:{name}` (or `function:{path}:{ClassName}.{method}` for methods).
- **Every edge goes through `is_valid_edge()`** (`graph/schema/graph_schema.py`)
  before being added to a result.
- **Confidence-bearing fields validate their own range** in `__post_init__` (see
  `GraphEdge`, `FrameworkInfo`, `ImpactedNode`).
- **AI-calling code takes the model call as an injected parameter**
  (`llm_call: Callable[[str], str]`), not a direct import — keeps it testable independently.
- **Files can go missing or get corrupted between generation and save.** If something
  inexplicably `ModuleNotFoundError`s or behaves like an older version, check the file
  was actually saved correctly before assuming the code is wrong.

## Open decisions — don't assume, ask the user

- **`cache/`** — user wants this, hasn't explained the design yet. Ask before building.
- **`analysis/dead_code/` — CAVEAT**: dead-code detection's entry-point heuristic
  (static candidates + content-based scans) is a documented limitation, not a bug.
  The real fix would be parsing route decorators (`@app.get(...)`) into the graph itself.
  Current caveat is explicitly tracked in `DeadCodeAnalysisResult.caveat` field so
  results travel with this context.

## Build status summary

Core flagship loop is closed and substantially extended past MVP: parse repo (Python
+ TypeScript/TSX/JS/JSX, including resolved `self`/`this` attribute calls) → build
graph → persist → resolve name to node → run impact analysis → narrate via real model
provider with free/paid fallback → expose over real API with non-blocking background
indexing (202 + polling) → view in real browser via real, externally-built Next.js
frontend — all tested against user's real `backend/` and `frontend/` folders on real
Windows machine, not mocks.

**Also done since prior version:** background indexing (closes known gap), workspace-ui
from-scratch rewrite (Next.js, replacing retired Vite), real backend-integration adapter
layer, interaction-bug fixes, live-discovered-and-fixed entry-point detection bug (was
returning empty, causing 124/124 false positives).

**Genuinely open / not built:** `ai/context_builder/`/`reasoning/`/`prompts/` (blocks
general repo Q&A), `query/text/` (embeddings search), `frameworks/*` (makes `Service`/
`Feature`/etc. real), `cache/`/`events/`/`lifecycle/` (cache design pending), zip upload,
GitLab/Bitbucket, comprehensive automated test suite (huge gap as codebase grows).

## Known gaps — real, not yet fixed

- **No API auth** — anyone who can reach the API can index/query any repo. Fine for
  local single-user use; real gap before shared/hosted deployment.
- **Frontend polling loop for `202`/background indexing not yet built** — `workspace-ui`
  still assumes `/graph` 404 means "not indexed" rather than "indexing in progress,"
  showing a misleading error during the (usually short) indexing window. Correct fix:
  poll `GET /repositories/{id}` until `index_status == "Ready"` before hitting `/graph`.
- **`workspace-ui` performance/visuals at real scale** — not stress-tested beyond
  ~300 nodes/edges. User's v0.dev version has better-engineered spatial-grid layout
  (O(n)) but hasn't been explicitly stress-tested either. User deferred UI/performance
  polish to later.
- **`tests/` is genuinely empty.** Every verification so far was one-off scripts.
  Worth prioritizing a real regression suite before the codebase grows much bigger.

