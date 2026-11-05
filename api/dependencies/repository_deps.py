
"""
Shared owner-aware repository dependencies.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import HTTPException

from graph.schema.node_types import GraphNode
from graph.schema.edge_types import GraphEdge
from repository.manager import registry
from repository.manager.repository_manager import load_record
from storage.graph.json_store import load_graph
from storage.repository_profile.profile_store import load_profile
from storage.repository_profile.types import RepositoryProfile


def get_storage_dir_or_404(
    repository_id: str,
    user_id: str,
) -> Path:
    storage_dir = registry.lookup(repository_id, user_id)

    if storage_dir is None:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        )

    storage_path = Path(storage_dir).resolve()
    record = load_record(storage_path)

    if record is None or record.owner_id != user_id:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        )

    return storage_path


def load_graph_for_repo(
    repository_id: str,
    user_id: str,
) -> tuple[list[GraphNode], list[GraphEdge]]:
    storage_dir = get_storage_dir_or_404(repository_id, user_id)
    graph_path = storage_dir / "graph.json"

    if not graph_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Repository graph is not available yet.",
        )

    nodes, edges, _metadata = load_graph(graph_path)
    return nodes, edges


def load_profile_for_repo(
    repository_id: str,
    user_id: str,
) -> RepositoryProfile:
    storage_dir = get_storage_dir_or_404(repository_id, user_id)
    profile_path = storage_dir / "profile.json"

    if not profile_path.exists():
        raise HTTPException(
            status_code=404,
            detail="Repository profile is not available yet.",
        )

    return load_profile(profile_path)
