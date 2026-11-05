"""
Top-level schema contract: graph snapshot metadata + validation rules.

Import from here (not by hardcoding strings like "Function" or "calls")
anywhere in builder/, analysis/, query/, or ai/ — this file plus
node_types.py and edge_types.py are the single source of truth for what
the graph is allowed to contain.
"""

from dataclasses import dataclass, field
from datetime import datetime

from .edge_types import EdgeType
from .node_types import NodeType
from .schema_version import SCHEMA_VERSION

# Which (from_type, to_type) pairs are valid for each edge type.
# The builder should validate against this before writing an edge —
# catches parser bugs early instead of producing a corrupt graph.
VALID_EDGE_ENDPOINTS: dict[EdgeType, list[tuple[NodeType, NodeType]]] = {
    EdgeType.CONTAINS: [
        (NodeType.REPOSITORY, NodeType.MODULE),
        (NodeType.MODULE, NodeType.FILE),
        (NodeType.FILE, NodeType.CLASS),
        (NodeType.FILE, NodeType.FUNCTION),
        (NodeType.FILE, NodeType.INTERFACE),
        (NodeType.CLASS, NodeType.FUNCTION),
    ],
    EdgeType.IMPORTS: [
        (NodeType.FILE, NodeType.FILE),
        (NodeType.FILE, NodeType.MODULE),
    ],
    EdgeType.CALLS: [
        (NodeType.FUNCTION, NodeType.FUNCTION),
    ],
    EdgeType.INHERITS: [
        (NodeType.CLASS, NodeType.CLASS),
    ],
    EdgeType.IMPLEMENTS: [
        (NodeType.CLASS, NodeType.INTERFACE),
    ],
    EdgeType.EXPOSES: [
        (NodeType.FUNCTION, NodeType.ENDPOINT),
    ],
    EdgeType.READS: [
        (NodeType.FUNCTION, NodeType.ENTITY),
        (NodeType.REPOSITORY_LAYER, NodeType.ENTITY),
    ],
    EdgeType.WRITES: [
        (NodeType.FUNCTION, NodeType.ENTITY),
        (NodeType.REPOSITORY_LAYER, NodeType.ENTITY),
    ],
    EdgeType.TESTS: [
        (NodeType.TEST, NodeType.FUNCTION),
        (NodeType.TEST, NodeType.CLASS),
    ],
    EdgeType.DEPENDS_ON: [
        (NodeType.FUNCTION, NodeType.EXTERNAL_DEPENDENCY),
        (NodeType.FILE, NodeType.EXTERNAL_DEPENDENCY),
    ],
    EdgeType.BELONGS_TO_FEATURE: [
        (NodeType.FUNCTION, NodeType.FEATURE),
        (NodeType.FILE, NodeType.FEATURE),
        (NodeType.CLASS, NodeType.FEATURE),
    ],
    EdgeType.CONFIGURES: [
        (NodeType.CONFIGURATION, NodeType.SERVICE),
        (NodeType.CONFIGURATION, NodeType.FUNCTION),
    ],
    EdgeType.REFERENCES: [
        (NodeType.FUNCTION, NodeType.CLASS),
        (NodeType.FUNCTION, NodeType.INTERFACE),
        (NodeType.FUNCTION, NodeType.FUNCTION),
        (NodeType.FILE, NodeType.FUNCTION),
    ],
}


def is_valid_edge(edge_type: EdgeType, from_type: NodeType, to_type: NodeType) -> bool:
    """Builder calls this before writing any edge to the graph store."""
    allowed = VALID_EDGE_ENDPOINTS.get(edge_type, [])
    return (from_type, to_type) in allowed


@dataclass
class GraphSnapshotMetadata:
    """
    Attached to every persisted graph. Lets every consumer (query, ai,
    api) know exactly which schema version and which commit produced
    the graph they're reading — required before trusting any result.
    """

    schema_version: str = SCHEMA_VERSION
    graph_id: str = ""              # stable id for this graph instance (one per repository)
    graph_hash: str = ""            # content hash of the full node/edge set — lets consumers
                                     # cheaply detect "has anything changed" without diffing
    repository_id: str = ""
    repository_commit: str = ""
    generated_at: datetime = field(default_factory=datetime.utcnow)
    