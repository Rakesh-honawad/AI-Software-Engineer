
"""
api/routes/explain.py

General-purpose explanation for any repository graph node.

Reads only the node's line_range when available, reducing the amount
of source sent to the model. Repository access is restricted to the
authenticated Supabase user.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ai.narration.node_explain_narration import (
    build_explain_node_prompt,
    narrate_node_explanation,
)
from ai.router.orchestrator import run_known_task
from ai.router.router_v2 import AllFamiliesFailedError
from api.dependencies.repository_deps import (
    get_storage_dir_or_404,
    load_graph_for_repo,
)
from auth.dependencies import AuthenticatedUser, get_current_user
from entitlements.service import AccessDeniedError, check_access
from graph.algorithms.impact.traversal import DEPENDS_ON_EDGE_TYPES
from repository.manager.repository_manager import load_record

router = APIRouter(prefix="/repositories", tags=["explain"])


class ExplainNodeRequest(BaseModel):
    node_id: str


class ExplainNodeResponse(BaseModel):
    node_id: str
    name: str
    file_path: str | None
    explanation: str


def _read_scoped_source(
    local_path: str,
    file_path: str,
    line_range: tuple[int, int] | None,
) -> str:
    repository_root = Path(local_path).resolve()
    source_path = (repository_root / file_path).resolve()

    # Prevent a graph file path from escaping the repository directory.
    try:
        source_path.relative_to(repository_root)
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail="The node's source path is outside the repository.",
        ) from e

    try:
        text = source_path.read_text(encoding="utf-8", errors="ignore")
    except OSError as e:
        raise HTTPException(
            status_code=404,
            detail=f"Could not read source file: {e}",
        ) from e

    if line_range is None:
        return text

    lines = text.splitlines()
    start, end = line_range

    # 1-indexed, inclusive. Clamp defensively if the file has changed
    # since the graph was indexed.
    start = max(1, start)
    end = min(len(lines), end)

    if start > end:
        return text

    return "\n".join(lines[start - 1 : end])


@router.post("/{repository_id}/explain", response_model=ExplainNodeResponse)
async def explain_node(
    repository_id: str,
    request: ExplainNodeRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> ExplainNodeResponse:
    """
    General-purpose "what does this code do" endpoint.

    Distinct from /impact/narrate, which explains what breaks if a
    node changes, and /dead-code/explain, which evaluates dead code.
    """
    try:
        service_tier = check_access(user.id)
    except AccessDeniedError as e:
        code = 402 if "expired" in e.message.lower() else 403
        raise HTTPException(status_code=code, detail=e.message) from e

    # Resolve repository data using the authenticated owner ID.
    storage_dir = get_storage_dir_or_404(repository_id, user.id)
    nodes, edges = load_graph_for_repo(repository_id, user.id)

    record = load_record(storage_dir)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No record found for '{repository_id}'.",
        )

    # Defense in depth: verify the loaded record's ownership.
    if record.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Repository not found.")

    node = next((n for n in nodes if n.id == request.node_id), None)
    if node is None:
        raise HTTPException(
            status_code=404,
            detail=f"'{request.node_id}' not found in this repository's graph.",
        )

    if not node.file_path:
        raise HTTPException(
            status_code=400,
            detail="This node has no file_path to read source from.",
        )

    source_text = _read_scoped_source(
        record.local_path,
        node.file_path,
        node.line_range,
    )

    fan_in = sum(
        1
        for edge in edges
        if edge.to_id == node.id and edge.type in DEPENDS_ON_EDGE_TYPES
    )

    prompt = build_explain_node_prompt(
        node_name=node.name,
        node_type=node.type.value,
        file_path=node.file_path,
        source_text=source_text,
        fan_in=fan_in,
    )

    async def llm_call(p: str) -> str:
        result = await run_known_task(
            task_type="explain_node",
            service_tier=service_tier,
            system=(
                "You are an expert software engineer explaining real code "
                "to another developer. Be specific and concrete, never generic."
            ),
            prompt=p,
        )
        return result["final_text"]

    try:
        explanation = await narrate_node_explanation(prompt, llm_call)
    except AllFamiliesFailedError as e:
        raise HTTPException(
            status_code=503,
            detail=f"No AI model is currently available to explain this: {e}",
        ) from e

    return ExplainNodeResponse(
        node_id=node.id,
        name=node.name,
        file_path=node.file_path,
        explanation=explanation,
    )
