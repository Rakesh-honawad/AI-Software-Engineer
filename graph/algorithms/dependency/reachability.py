"""
Forward reachability — pure graph theory, no domain interpretation.
Mirrors graph/algorithms/impact/traversal.py's shape exactly, just
inverted: instead of "what depends on this node" (reverse BFS from one
changed node), this is "what can this SET of root nodes reach" (forward
BFS from many roots at once). Same no-severity, no-AI, "only answers
what's reachable" boundary as traversal.py.

Reusable beyond dead-code analysis — any future feature needing
"is X reachable from a known-live set of entry points" (e.g. a real
feature-tracing analysis, per graph/algorithms/feature_tracing/'s
still-stubbed folder) can use this directly instead of reimplementing
BFS.
"""

from __future__ import annotations

from graph.schema.edge_types import EdgeType, GraphEdge

# Edge types that represent "usage propagates reachability forward":
# Edge types that represent "usage propagates reachability forward":
# CALLS is the primary signal (A calls B => B is reachable if A is).
# INHERITS/IMPLEMENTS included so a base class isn't falsely flagged
# unreachable just because only its subclass is directly called —
# using a subclass implies the base is in use too.
# REFERENCES included so a type/name reference without a call (JSX
# component usage, a class referenced via instantiation, a
# module-level registry/lookup-table entry like
# `const PANELS = { budget: BudgetPanel }`) also propagates
# reachability — these are real usage, not structural noise.
# Deliberately excludes CONTAINS (structural, not usage — same
# exclusion reasoning as impact traversal.py) and IMPORTS (importing a
# module doesn't mean every symbol in it is used). READS/WRITES/
# DEPENDS_ON/CONFIGURES/TESTS/EXPOSES/BELONGS_TO_FEATURE point at
# non-candidate node types for dead-code purposes — see
# dead_code_analysis.py's CANDIDATE_TYPES — so propagating through
# them wouldn't change which candidate nodes are marked reachable.
DEFAULT_REACHABILITY_EDGE_TYPES = frozenset({
    EdgeType.CALLS, EdgeType.INHERITS, EdgeType.IMPLEMENTS, EdgeType.REFERENCES,
})

def forward_reachability(
    root_ids: set[str],
    edges: list[GraphEdge],
    edge_types: frozenset[EdgeType] = DEFAULT_REACHABILITY_EDGE_TYPES,
) -> set[str]:
    """BFS forward from every id in root_ids simultaneously, following
    only the given edge types, from_id -> to_id. Returns the full set
    of reached node ids, INCLUDING the roots themselves (a root is
    trivially reachable from itself).
    """
    adjacency: dict[str, list[str]] = {}
    for edge in edges:
        if edge.type not in edge_types:
            continue
        adjacency.setdefault(edge.from_id, []).append(edge.to_id)

    reached: set[str] = set(root_ids)
    queue: list[str] = list(root_ids)
    while queue:
        current = queue.pop()
        for neighbor in adjacency.get(current, []):
            if neighbor not in reached:
                reached.add(neighbor)
                queue.append(neighbor)

    return reached