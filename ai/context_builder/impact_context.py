"""
ai/context_builder/impact_context.py

Fixes the gap identified this session: impact_narration.py's prompt only
ever included node NAMES and graph relationships (distance, fan_in,
severity) — never the actual source code of the impacted nodes. That
meant the model could describe the shape of the dependency graph but
couldn't reason about whether a dependency was trivial or load-bearing,
because it never saw what the code actually does.

STRATEGY (kept deliberately simple, token-budget aware):
  - The changed node's own source is always included in full (bounded).
  - impacted_nodes is already sorted by risk_score descending (see
    analyze_impact() in analysis/impact/impact_analysis.py) — so the
    first N are already "the ones that matter most." Real source goes
    in for those N.
  - Everything beyond N gets name + relationship only, same as today —
    a change touching 50 nodes shouldn't blow the context budget just
    because they're all technically "impacted."
  - HARD RULE PRESERVED: this module only builds context, never calls
    a model. Matches the existing analysis/ai boundary discipline
    documented in impact_analysis.py and dead_code_narration.py.

WHY file_path IS RE-DERIVED HERE RATHER THAN CARRIED ON ImpactedNode:
ImpactedNode (contracts/analysis/impact_result.py) is constructed in
analyze_impact() without a file_path field — I don't have that file's
exact definition, so rather than assume a field that may not exist,
this module takes the already-loaded `nodes: list[GraphNode]` (the
route already has this from load_graph_for_repo) and looks up
file_path from there via node_id. If ImpactedNode DOES carry file_path
already, this still works identically — it just means the node_by_id
lookup below is redundant, not wrong.
"""

from __future__ import annotations

from pathlib import Path

from contracts.analysis.impact_result import ImpactAnalysisResult, ImpactedNode
from graph.schema.node_types import GraphNode

# How many of the highest-risk impacted nodes get real source pulled.
# Kept small on purpose — these are prepended to an already-detailed
# prompt (see impact_narration.py's build_narration_prompt), and every
# extra node here is real token cost on every /impact/narrate call.
DEFAULT_MAX_FULL_SOURCE_NODES = 5

# Per-node character cap when reading source — same spirit as
# dead_code_narration.py's MAX_SOURCE_CHARS, applied per-node here
# since multiple nodes' source can appear in one prompt.
DEFAULT_MAX_CHARS_PER_NODE = 1500

# Changed node gets a larger budget than each impacted node — it's the
# one thing every other node in the prompt is being compared against.
DEFAULT_MAX_CHARS_CHANGED_NODE = 3000


def _read_source_snippet(local_path: Path, file_path: str, max_chars: int) -> str | None:
    """Best-effort source read. Returns None (not an exception) on any
    failure — a missing/unreadable file should degrade the context, not
    break narration entirely. Same resilience posture as dead_code.py's
    route, which already handles OSError explicitly for this reason."""
    try:
        text = (local_path / file_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if len(text) > max_chars:
        return text[:max_chars] + f"\n[...truncated at {max_chars} characters...]"
    return text


def build_impact_context(
    result: ImpactAnalysisResult,
    nodes: list[GraphNode],
    local_path: Path,
    max_full_source_nodes: int = DEFAULT_MAX_FULL_SOURCE_NODES,
    max_chars_per_node: int = DEFAULT_MAX_CHARS_PER_NODE,
    max_chars_changed_node: int = DEFAULT_MAX_CHARS_CHANGED_NODE,
) -> str:
    """
    Pure string construction — no model call, no side effects, testable
    without AI. Returns a block of real source code (changed node +
    top-N impacted nodes by risk) to prepend to the existing
    name/relationship prompt in impact_narration.py.

    Returns "" (not None) if no source could be read for anything —
    callers should treat that as "context unavailable," not an error;
    narration still works with names/relationships alone, same as
    before this module existed.
    """
    node_by_id = {n.id: n for n in nodes}
    sections: list[str] = []

    changed_node = node_by_id.get(result.changed_node_id)
    if changed_node is not None and getattr(changed_node, "file_path", None):
        snippet = _read_source_snippet(local_path, changed_node.file_path, max_chars_changed_node)
        if snippet:
            sections.append(
                f"=== CHANGED NODE: {result.changed_node_name} ({changed_node.file_path}) ===\n{snippet}"
            )

    top_impacted: list[ImpactedNode] = result.impacted_nodes[:max_full_source_nodes]
    for impacted in top_impacted:
        node = node_by_id.get(impacted.node_id)
        if node is None or not getattr(node, "file_path", None):
            continue
        snippet = _read_source_snippet(local_path, node.file_path, max_chars_per_node)
        if snippet:
            sections.append(
                f"=== IMPACTED ({impacted.severity.value.upper()} risk): "
                f"{impacted.name} ({node.file_path}) ===\n{snippet}"
            )

    if not sections:
        return ""

    return (
        "REAL SOURCE CODE for the changed node and its highest-risk dependents "
        "(remaining impacted nodes beyond this list are summarized by name/relationship only "
        "below — use this source to judge whether a dependency is trivial or load-bearing, "
        "not just that a dependency exists):\n\n" + "\n\n".join(sections)
    )