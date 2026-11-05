# Workspace UI v2 — Next.js (supersedes v1, Vite)

Source of truth is the code in `workspace-ui/` (Next.js App Router
project — `app/`, `components/workspace/`, `lib/graph/`, `services/`).

**This replaces the earlier Vite/React `workspace-ui/` +
`workspace-ui-app/` pair described in the original `workspace_ui_v1.md`.**
That version is retired — deleted from the repo, not kept alongside
this one. If you find any reference to `workspace-ui-app/`,
`GraphCanvas.jsx`, `useForceLayout.js` (the `.jsx`/`.js` versions), or
Vite config elsewhere in old notes, it's stale; this doc and the
actual `.tsx` files in `workspace-ui/` are current.

## Why it was rebuilt, not just extended

The Vite version was functionally correct but visually minimal
(plain circles, straight thin edges, no drag, basic zoom) and was
never wired to the real backend at all — it only ever rendered
synthetic mock data. A full visual/interaction pass was scoped as a
v0 (Vercel) generation, which produces Next.js projects — so the
decision was made to let v0 rebuild the whole workspace fresh
(better node shapes, smooth zoom/pan, draggable nodes, minimap,
resizable AI panel, bottom feature dock) rather than port that
generated code backward into Vite by hand. The v0 output was then
integrated with the REAL backend (this doc's "Real data integration"
section) — v0 cannot write Python or design API contracts, so that
adapter layer is hand-built.

## Routes

- **`/`** — demo mode. Renders `<EngineerWorkspace />` with no
  `repositoryId`, which falls back to `lib/graph/data.ts`'s
  `generateGraph()` synthetic mock. No backend calls. Good for
  UI-only iteration without a running knowledge-engine process.
- **`/repo/[repositoryId]`** — live mode. Renders
  `<EngineerWorkspace repositoryId={...} />`, which fetches the REAL
  graph via `GET /repositories/{id}/graph` and never silently falls
  back to mock data on a real fetch failure (a failed real fetch
  surfaces as a visible error state, per `useRepositoryGraph`'s
  design in `engineer-workspace.tsx`).

## Real data integration — the adapter layer

Because v0 doesn't know the real backend's field names, it invented
its own display-oriented shape (`lib/graph/data.ts`'s `GraphNode`/
`GraphEdge`/`ImpactData`) with fields like `module`, `fan_in`,
`is_tested` living directly on the node. The real backend's actual
shape (confirmed LIVE, not just from docs — the wire format is
PascalCase, e.g. `"File"` not `"file"`, which the docs never showed
explicitly) is different in several load-bearing ways:

| v0's invented shape | Real backend | Reconciliation |
|---|---|---|
| `node.fan_in`, `node.is_tested` always present | Only exist on `ImpactedNode` (i.e. only known once impact analysis has run for that node) | `GraphNode.fan_in`/`is_tested` made `undefined`-capable; UI renders "Run Check connections to see" / "Unknown until analyzed" instead of a fake 0/false |
| `node.module` field | Doesn't exist on the real `GraphNode` | Derived client-side from `file_path` (best-effort directory grouping), never sent by the backend |
| `ImpactData.narration` baked into the impact result | Real `/impact` is structured-only, no narration (`analysis/` never imports `ai/`); narration is a SEPARATE `/impact/narrate` call | `adaptImpact()` leaves `narration: ""`; a separate `adaptImpactWithNarration()`/`mergeNarration()` handle the narrate-endpoint case |
| `type: "function" \| "class" \| "file"` only | 7 real types (`Function`/`Class`/`File`/`Module`/`Service`/`ExternalDependency`/`Feature`) | `geometry.ts` has a distinct shape for all 7; `adapters.ts` maps case-insensitively, no collapsing |
| No edge `type` | Real edges carry `calls`/`contains`/`inherits`/etc. | Preserved through the adapter; `contains` edges render as quiet dashed lines (structural, not a dependency signal) instead of being dropped |

All of this lives in `lib/graph/api-types.ts` (the real wire-format
types, kept deliberately separate from the UI's display types — same
separation-of-concerns reasoning as `api/schemas` vs. storage
internals elsewhere in this project) and `lib/graph/adapters.ts` (the
mapping functions — the only files that import both type sets).

`services/api-client.ts` is the thin fetch wrapper over the real
endpoints (`getGraph`, `getImpact`, `narrateImpact`, `getDeadCode`,
`explainDeadCode`, `indexRepository`, `getRepositoryStatus`). Base URL
comes from `NEXT_PUBLIC_KNOWLEDGE_ENGINE_API_URL` in `.env.local`.

## Components

| File | Role |
|---|---|
| `app/repo/[repositoryId]/page.tsx` | Live-mode entry point |
| `app/page.tsx` | Demo-mode entry point |
| `components/workspace/engineer-workspace.tsx` | Top-level orchestrator — real vs. demo data switch, selection state, impact-mode state, dead-code highlight state |
| `components/workspace/graph-canvas.tsx` | SVG rendering, 7 node-type shapes, bezier edges with directional flow-pulse animation, draggable nodes, smooth zoom/pan, minimap, search-match highlighting |
| `components/workspace/use-force-layout.ts` | Client-side force-directed layout (no d3 dependency — matches the "start simple" pattern used elsewhere, e.g. the backend's own degree-count-not-real-centrality Tier 2 ranking) |
| `components/workspace/node-inspector.tsx` | Right-docked facts panel; honest "unknown" states for `fan_in`/`is_tested` outside an active impact analysis |
| `components/workspace/node-action-menu.tsx` | Click-node popover: Open file / Check connections / Explain this node / Connect to AI agent |
| `components/workspace/ai-panel.tsx` | Resizable docked/half-screen AI panel — honestly scoped to impact narration for a selected node, explicitly declines general repo Q&A (not built — see `ai/context_builder/`, `ai/retriever/`, `ai/reasoning/`, all still empty) |
| `components/workspace/feature-dock.tsx` | Bottom icon dock: Files, Impact analysis, History (honest placeholder — no backend endpoint exists), Dead code (real, wired), Layers/Branches (decorative, not wired), Add tool (placeholder) |
| `components/workspace/top-bar.tsx` | Repo name/branch, search, node/edge counts |
| `lib/graph/geometry.ts` | Pure layout math + per-node-type SVG shape paths |

## Dead-code panel (feature-dock.tsx)

Real, fully wired: "Scan repository" calls `GET .../dead-code`, shows
the ALWAYS-visible caveat banner (never collapsed/hidden — matches the
backend's own insistence that this travels with the data), a
"Highlight on graph" toggle that dashes/dims dead nodes on the canvas,
and a per-finding "Explain" button that calls
`POST .../dead-code/explain` and renders the AI's reasoning (or an
honest "no AI model configured" message) inline under that row.
Findings show a fully-qualified label (e.g. `CacheManager.set`,
derived from the node id's own structure) rather than the bare method
name, since identical method names across different classes (`set`/
`get`/`clear` in multiple cache-provider classes, seen live against
`repo:miniai-backend`) are otherwise ambiguous in a flat list.

## Known interaction fixes worth knowing about (real bugs, already fixed)

- **Whole-page zoom on trackpad pinch** — the wheel handler was
  originally wired as a JSX `onWheel` prop; React registers wheel
  listeners as passive by default, so `e.preventDefault()` silently
  failed for the pinch case (Chrome maps a two-finger pinch to a wheel
  event with `ctrlKey: true`), letting the browser's native page-zoom
  fire simultaneously with the canvas's own zoom. Fixed by manually
  attaching the listener via `useEffect` with `{ passive: false }`.
- **Laggy/stuttering wheel and pinch zoom** — `zoomAt()` was starting
  a fresh eased tween (`animateTo` with a 90-150ms duration) on every
  single wheel event; a continuous gesture fires many events per
  second, so each one cancelled the previous half-finished animation
  and restarted a new one chasing a moving target. Fixed: wheel/pinch
  now apply instantly (`dur=0`), no easing — the physical gesture
  already provides its own smoothness. Button-triggered zoom, "fit to
  screen", and "focus node" still animate, since those are discrete
  one-shot actions where easing is correct.
- **Minimap/zoom controls pushed off-screen on short windows** — only
  width-based responsive breakpoints existed (900px/640px); nothing
  adapted to window HEIGHT. Added `max-height: 760px`/`600px`
  breakpoints in `globals.css`.
- **Dead-code panel covering the highlighted graph** — the dock panel
  system defaults to horizontally centered, directly over the middle
  of the canvas. Added a `.em-dock-panel--left` variant, applied only
  to the dead-code panel (the one panel where seeing highlighted graph
  state behind it while reading matters most).

## Verified

- Full real-data pipeline confirmed live end-to-end against
  `repo:miniai-backend`: index → poll status → fetch graph → render
  154 real nodes / 272 real edges → select a real node → inspector
  shows real facts → dead-code scan → 70 real findings → highlight on
  graph → per-finding Explain (confirmed reaching the AI router and
  correctly 503ing with no provider configured).
- Draggable nodes, smooth zoom/pan (post-fix), minimap navigation, top
  search bar, resizable AI panel — all confirmed working in a real
  browser against real data (not just mocks).

## Current status

- ✅ Real backend integration — done, verified live, adapter layer
  documented above.
- ✅ Dead-code UI — done, verified live.
- ✅ Zoom/pan/panel-positioning bugs — fixed, per the list above.
- ❌ Background-indexing polling loop — `useRepositoryGraph` still
  treats a `/graph` 404 as a hard error rather than "indexing in
  progress, poll `/repositories/{id}` and retry" (see
  `background_indexing_v1.md`'s frontend-implication note).
- ❌ `Check connections` and `Open file` action-menu buttons — wired,
  never actually click-tested as of this doc.
- ❌ `Layers` and `Branches` dock icons — purely decorative, `onClick`
  is a no-op. Visually identical in styling to the real, working
  icons next to them — a real UX gap (nothing distinguishes "wired"
  from "placeholder" at a glance).
- ❌ Draggable node position persistence (resets on reload), real
  `get_neighbors`-backed "Check connections" (currently just
  re-triggers impact analysis), "find this feature" command-driven
  node regrouping, frontend-left/backend-right directional layout,
  real multi-agent "Connect to AI agent" (currently seeds an
  impact-narration prompt) — none built.
- ❌ General repo Q&A in the AI panel — honestly out of scope until
  `ai/context_builder/`/`ai/retriever/`/`ai/reasoning/` exist.
- ❌ No AI provider ever live-tested (same gap as `model_router_v1.md`
  and `dead_code_analysis_v1.md` — deliberately deferred).