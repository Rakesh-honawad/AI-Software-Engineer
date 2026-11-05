"""
Graph persistence — JSON-file backend, Phase 1.

DESIGN NOTE — why JSON, not Kuzu, right now:
The blueprint's target is Kuzu (embedded graph DB). This module
deliberately starts simpler: a JSON file per repository. Same reason
as the degree-based ranking in tier2_backfill and the dependency-
injected model call in narration — get something correct and testable
today, swap the implementation later without changing callers. The
function signatures here (save_graph/load_graph/compute_graph_hash)
are the contract a future Kuzu-backed module would keep; only the
body changes.

DESIGN NOTE — graph_hash and schema versioning:
Every save computes a content hash (sha256 over sorted node/edge data)
and stores it in GraphSnapshotMetadata.graph_hash. load_graph()
recomputes it on load and compares — if the file was hand-edited or
corrupted, this catches it immediately rather than silently loading
bad data. load_graph() also checks the file's schema_version against
the current SCHEMA_VERSION and raises if they don't match, rather
than guessing how to interpret an old shape.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from graph.schema.edge_types import EdgeSource, EdgeType, GraphEdge
from graph.schema.graph_schema import GraphSnapshotMetadata
from graph.schema.node_types import GraphNode, NodeSource, NodeType
from graph.schema.schema_version import SCHEMA_VERSION


class SchemaVersionMismatch(Exception):
    """Raised when a saved graph's schema_version doesn't match the
    currently-running code's SCHEMA_VERSION. Deliberately NOT auto-
    migrated — silently reinterpreting an old shape under a new schema
    is how graphs get silently corrupted."""


class GraphIntegrityError(Exception):
    """Raised when a loaded file's stored graph_hash doesn't match the
    hash recomputed from its own contents — the file was edited,
    truncated, or corrupted after being saved."""


def _serialize_node(node: GraphNode) -> dict:
    return {
        "id": node.id,
        "type": node.type.value,
        "name": node.name,
        "properties": node.properties,
        "file_path": node.file_path,
        "line_range": list(node.line_range) if node.line_range else None,
        "source": node.source.value,
        "created_at": node.created_at.isoformat(),
        "updated_at": node.updated_at.isoformat(),
        "is_deleted": node.is_deleted,
    }


def _deserialize_node(d: dict) -> GraphNode:
    return GraphNode(
        id=d["id"],
        type=NodeType(d["type"]),
        name=d["name"],
        properties=d.get("properties", {}),
        file_path=d.get("file_path"),
        line_range=tuple(d["line_range"]) if d.get("line_range") else None,
        source=NodeSource(d["source"]),
        created_at=datetime.fromisoformat(d["created_at"]),
        updated_at=datetime.fromisoformat(d["updated_at"]),
        is_deleted=d.get("is_deleted", False),
    )


def _serialize_edge(edge: GraphEdge) -> dict:
    return {
        "from_id": edge.from_id,
        "to_id": edge.to_id,
        "type": edge.type.value,
        "confidence": edge.confidence,
        "source": edge.source.value,
        "created_by": edge.created_by,
    }


def _deserialize_edge(d: dict) -> GraphEdge:
    return GraphEdge(
        from_id=d["from_id"],
        to_id=d["to_id"],
        type=EdgeType(d["type"]),
        confidence=d.get("confidence", 1.0),
        source=EdgeSource(d.get("source", "AST")),
        created_by=d.get("created_by"),
    )


def compute_graph_hash(nodes: list[GraphNode], edges: list[GraphEdge]) -> str:
    """Deterministic content hash — same graph contents always produce
    the same hash regardless of list order, so callers can cheaply
    check "has anything changed" without a full diff.
    """
    node_repr = sorted(
        json.dumps(_serialize_node(n), sort_keys=True) for n in nodes
    )
    edge_repr = sorted(
        json.dumps(_serialize_edge(e), sort_keys=True) for e in edges
    )
    combined = "\n".join(node_repr) + "\n---\n" + "\n".join(edge_repr)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


def save_graph(
    path: str | Path,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    repository_id: str,
    repository_commit: str,
    graph_id: str = "",
) -> GraphSnapshotMetadata:
    """Writes nodes/edges/metadata to a single JSON file. Returns the
    metadata object that was written, so the caller can persist
    graph_hash/generated_at elsewhere (e.g. in a RepositoryProfile)
    without re-reading the file.
    """
    graph_hash = compute_graph_hash(nodes, edges)
    metadata = GraphSnapshotMetadata(
        schema_version=SCHEMA_VERSION,
        graph_id=graph_id or repository_id,
        graph_hash=graph_hash,
        repository_id=repository_id,
        repository_commit=repository_commit,
    )

    data = {
        "metadata": {
            "schema_version": metadata.schema_version,
            "graph_id": metadata.graph_id,
            "graph_hash": metadata.graph_hash,
            "repository_id": metadata.repository_id,
            "repository_commit": metadata.repository_commit,
            "generated_at": metadata.generated_at.isoformat(),
        },
        "nodes": [_serialize_node(n) for n in nodes],
        "edges": [_serialize_edge(e) for e in edges],
    }

    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")
    return metadata


def load_graph(
    path: str | Path,
    verify_integrity: bool = True,
) -> tuple[list[GraphNode], list[GraphEdge], GraphSnapshotMetadata]:
    """Reads a graph back. Raises SchemaVersionMismatch if the file was
    written by a different schema version, and GraphIntegrityError if
    verify_integrity=True and the recomputed hash doesn't match the
    stored one.
    """
    data = json.loads(Path(path).read_text(encoding="utf-8"))

    stored_version = data["metadata"]["schema_version"]
    if stored_version != SCHEMA_VERSION:
        raise SchemaVersionMismatch(
            f"Graph at {path} was saved with schema_version={stored_version}, "
            f"but the running code is at SCHEMA_VERSION={SCHEMA_VERSION}. "
            f"Reindex the repository rather than loading this file as-is."
        )

    nodes = [_deserialize_node(d) for d in data["nodes"]]
    edges = [_deserialize_edge(d) for d in data["edges"]]

    if verify_integrity:
        recomputed_hash = compute_graph_hash(nodes, edges)
        stored_hash = data["metadata"]["graph_hash"]
        if recomputed_hash != stored_hash:
            raise GraphIntegrityError(
                f"Graph at {path} failed integrity check: stored hash "
                f"{stored_hash[:12]}... does not match recomputed hash "
                f"{recomputed_hash[:12]}.... File may have been edited or corrupted."
            )

    metadata = GraphSnapshotMetadata(
        schema_version=data["metadata"]["schema_version"],
        graph_id=data["metadata"]["graph_id"],
        graph_hash=data["metadata"]["graph_hash"],
        repository_id=data["metadata"]["repository_id"],
        repository_commit=data["metadata"]["repository_commit"],
        generated_at=datetime.fromisoformat(data["metadata"]["generated_at"]),
    )

    return nodes, edges, metadata