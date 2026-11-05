"""
Graph query — basic lookups over an in-memory node/edge list, shared
so callers (impact analysis already reimplements bits of this, the
workspace UI, the retriever) don't each write their own node-by-id
dict or neighbor walk. Nothing here is impact-analysis-specific; that
stays in analysis/impact/, which is domain interpretation, not lookup.
"""

from __future__ import annotations

from typing import Literal

from graph.schema.edge_types import EdgeType, GraphEdge
from graph.schema.node_types import GraphNode, NodeType

Direction = Literal["in", "out", "both"]


def get_node(node_id: str, nodes: list[GraphNode]) -> GraphNode | None:
    for node in nodes:
        if node.id == node_id:
            return node
    return None


def find_nodes_by_type(
    node_type: NodeType, nodes: list[GraphNode], include_deleted: bool = False
) -> list[GraphNode]:
    return [
        n for n in nodes
        if n.type == node_type and (include_deleted or not n.is_deleted)
    ]


def get_neighbors(
    node_id: str,
    edges: list[GraphEdge],
    direction: Direction = "both",
    edge_types: tuple[EdgeType, ...] | None = None,
) -> list[tuple[GraphEdge, str]]:
    """Returns [(edge, neighbor_node_id), ...] for edges touching
    node_id. direction='out' = edges where node_id is from_id
    (neighbor is to_id); direction='in' = the reverse; 'both' = either.
    """
    results: list[tuple[GraphEdge, str]] = []
    for edge in edges:
        if edge_types is not None and edge.type not in edge_types:
            continue
        if direction in ("out", "both") and edge.from_id == node_id:
            results.append((edge, edge.to_id))
        if direction in ("in", "both") and edge.to_id == node_id:
            results.append((edge, edge.from_id))
    return results