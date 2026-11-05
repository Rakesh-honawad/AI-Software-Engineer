"""
Reverse impact traversal — pure graph algorithm, no severity scoring,
no domain interpretation. Lives in graph/algorithms/, not analysis/,
because "which nodes are reachable backward from X" is a graph-theory
question, independent of what "risky" means for MiniAI specifically.

DIRECTION: for each edge type below, the edge points from the
DEPENDENT to the thing it depends on (e.g. CALLS: caller -> callee).
So "what's affected if I change X" means walking these edges
BACKWARD from X — finding everything that points AT X, then
everything that points at those, breadth-first.

CONTAINS is deliberately excluded — structural containment isn't a
"depends on" relationship for impact purposes (a file doesn't stop
working because one of its functions changed; the function is the
unit of change, not its container).

REFERENCES added this session — previously the only edge type that
ever pointed AT a class node was INHERITS (from a subclass), which
meant a class with zero subclasses always showed total_affected=0 in
impact analysis, no matter how many places actually instantiated and
used it (e.g. `mgr = CacheManager(); mgr.get_stats()` produced a CALLS
edge to get_stats but nothing pointing at CacheManager itself — see
graph/builder/python_builder.py's build_file_graph, which now emits a
REFERENCES edge from caller to class alongside that CALLS edge).
"""

from __future__ import annotations

from collections import deque

from graph.schema.edge_types import EdgeType, GraphEdge

DEPENDS_ON_EDGE_TYPES = (
    EdgeType.CALLS,
    EdgeType.READS,
    EdgeType.WRITES,
    EdgeType.INHERITS,
    EdgeType.IMPLEMENTS,
    EdgeType.REFERENCES,
    EdgeType.IMPORTS,
)


def reverse_impact_traversal(
    changed_node_id: str,
    edges: list[GraphEdge],
    edge_types: tuple[EdgeType, ...] = DEPENDS_ON_EDGE_TYPES,
    max_depth: int | None = None,
) -> dict[str, int]:
    """Returns {affected_node_id: distance}, distance=1 for direct
    dependents, distance=2 for their dependents, etc. changed_node_id
    itself is never included in the result.
    """
    reverse_adjacency: dict[str, list[str]] = {}
    for edge in edges:
        if edge.type in edge_types:
            reverse_adjacency.setdefault(edge.to_id, []).append(edge.from_id)

    distances: dict[str, int] = {}
    visited = {changed_node_id}
    queue: deque[tuple[str, int]] = deque([(changed_node_id, 0)])

    while queue:
        current_id, current_distance = queue.popleft()
        if max_depth is not None and current_distance >= max_depth:
            continue
        for dependent_id in reverse_adjacency.get(current_id, []):
            if dependent_id not in visited:
                visited.add(dependent_id)
                distances[dependent_id] = current_distance + 1
                queue.append((dependent_id, current_distance + 1))

    return distances