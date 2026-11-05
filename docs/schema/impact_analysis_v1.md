# Impact analysis + narration v1

Source of truth is the code in `graph/algorithms/impact/traversal.py`,
`analysis/impact/impact_analysis.py`, `contracts/analysis/impact_result.py`,
and `ai/narration/impact_narration.py`. This is the core flagship
feature: "developer changes a function, system explains what breaks."

## Pipeline

```
changed_node_id
    ↓  graph/algorithms/impact/traversal.py  (pure graph theory)
{affected_node_id: distance}  — reverse BFS over depends-on edges
    ↓  analysis/impact/impact_analysis.py    (domain scoring, NO AI)
ImpactAnalysisResult  — severity-bucketed, structured, contracts/analysis/
    ↓  ai/narration/impact_narration.py       (the ONLY AI touchpoint)
plain-English explanation for the developer
```

**Why four files across three folders, not one:** each layer changes
for a different reason. The traversal algorithm changes if graph
theory improves (e.g. swapping BFS for a smarter reachability
algorithm). The scoring changes if the risk model improves (new
signal, retuned weights). The narration changes if prompt wording
improves. None of those should force a change in either of the others
— that's the whole point of the `analysis/ never imports ai/` rule.

## Layer 1 — `graph/algorithms/impact/traversal.py`

`reverse_impact_traversal(changed_node_id, edges, edge_types=DEPENDS_ON_EDGE_TYPES, max_depth=None) -> dict[str, int]`

Reverse BFS: starting at the changed node, walks edges **backward**
(finds everything that points AT it) across `calls`, `reads`, `writes`,
`inherits`, `implements` — the edge types that represent "depends on."
Returns `{affected_node_id: distance}`, distance 1 = direct dependent.

**`contains` is deliberately excluded** — structural containment isn't
a "depends on" relationship for impact purposes; a file doesn't break
because one of its functions changed, the function itself is the unit
of change.

No severity, no domain interpretation — this file only answers "what's
reachable."

## Layer 2 — `analysis/impact/impact_analysis.py`

`analyze_impact(changed_node_id, nodes, edges) -> ImpactAnalysisResult`

Takes the traversal's raw distances and computes a `risk_score` (0.0–1.0)
per affected node:

```
risk_score = (DISTANCE_WEIGHT / distance)
           + FAN_IN_WEIGHT * min(fan_in, FAN_IN_CAP) / FAN_IN_CAP
           + (0.0 if tested else UNTESTED_PENALTY)
```

Current weights (starting values, not yet tuned against a real repo):
`DISTANCE_WEIGHT=0.4`, `FAN_IN_WEIGHT=0.3`, `FAN_IN_CAP=10`,
`UNTESTED_PENALTY=0.3`. Bucketed at `HIGH_THRESHOLD=0.66` /
`MEDIUM_THRESHOLD=0.33`.

**Verified behavior** (from the test scenario in this module's history):
a tested direct dependent scores *lower* than an untested one at the
same distance, and an untested two-hop dependent lands *between* the
two — i.e. both distance decay and the test-coverage bonus are doing
real work, not just present in the formula.

**Hard rule, enforced by not importing `ai` anywhere in this file:**
this layer never calls a model. `ImpactAnalysisResult` is the complete,
final, structured output of deterministic analysis.

Soft-deleted nodes are excluded from results. Raises `ValueError` if
`changed_node_id` isn't found in `nodes` — callers should resolve a
name to an id (via `query/`, not yet built) before calling this, so a
typo surfaces immediately rather than a silently-empty result.

## Contract — `contracts/analysis/impact_result.py`

`ImpactedNode`: `node_id`, `node_type`, `name`, `distance`, `fan_in`,
`is_tested`, `severity` (`low`/`medium`/`high`), `risk_score`.
`ImpactAnalysisResult`: `changed_node_id`, `changed_node_name`,
`impacted_nodes` (sorted by risk_score descending), plus
high/medium/low counts.

Lives in `contracts/`, not `analysis/`, because it's read by three
different layers (narration, workspace-ui, api) — none of which
should import `analysis/impact/`'s internals just to know this shape.

## Layer 3 — `ai/narration/impact_narration.py`

**The only place AI touches this feature.** Two functions:

- `build_narration_prompt(result) -> str` — pure string construction,
  no model call. Groups impacted nodes by severity, caps individually-
  listed nodes at `MAX_NODES_IN_PROMPT=15` (with a "and N more" note),
  explicitly instructs the model to explain and hint only — **never
  write code, never suggest a specific fix**. Handles the zero-impact
  case with a different, shorter prompt ("this looks safe to change").
- `narrate_impact(result, llm_call) -> str` — sends the prompt through
  `llm_call`, an injected `Callable[[str], str]`.

**Why dependency injection instead of importing `ai/router/` directly:**
the router (and the free-tier/paid-tier decision) doesn't exist yet.
This keeps narration fully built and testable today with a stub
function — zero network calls, zero API keys required. Whoever wires
up the router later passes it in as `llm_call`; nothing in this file
changes.

**Known gap, not yet closed:** the "don't write code" instruction is
enforced by prompt wording only, not a code-level guard — the actual
model output can't be constrained at the Python level. If this proves
unreliable once a real model is wired in, the fix is a stronger prompt
or a post-hoc check, not a change to the contract with `analysis/impact/`.

## Current status

- ✅ Traversal, scoring, contract, and narration prompt-building — all
  built and tested (hand-verified scoring math, soft-delete exclusion,
  missing-node guard, zero-impact prompt case).
- ✅ Narration wiring verified with a stub `llm_call`.
- ❌ Narration NOT tested against a real model response — no API key
  available where this was built; that's tied to the still-open
  router/model decision, not a gap in this code.
- ❌ No caller yet resolves a human-readable name (e.g. "the login
  function") to a `node_id` before calling `analyze_impact` — that's
  `query/symbol_index/`, not yet built.