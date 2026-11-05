
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from analysis.impact.impact_analysis import analyze_impact
from ai.context_builder.impact_context import build_impact_context
from ai.narration.impact_narration import narrate_impact
from ai.router.orchestrator import run_known_task
from ai.router.router_v2 import AllFamiliesFailedError
from api.dependencies.repository_deps import (
    get_storage_dir_or_404,
    load_graph_for_repo,
)
from api.schemas.impact_schemas import (
    ImpactAnalysisRequest,
    ImpactAnalysisResponse,
    ImpactedNodeResponse,
)
from auth.dependencies import AuthenticatedUser, get_current_user
from entitlements.service import AccessDeniedError, check_access
from graph.schema.node_types import NodeType
from query.symbol.symbol_search import (
    AmbiguousSymbolError,
    SymbolNotFoundError,
    resolve_symbol,
)
from repository.manager.repository_manager import load_record

router = APIRouter(
    prefix="/repositories/{repository_id}/impact",
    tags=["impact"],
)


def _to_response(
    result,
    narration: str | None = None,
) -> ImpactAnalysisResponse:
    return ImpactAnalysisResponse(
        changed_node_id=result.changed_node_id,
        changed_node_name=result.changed_node_name,
        impacted_nodes=[
            ImpactedNodeResponse(
                node_id=node.node_id,
                node_type=node.node_type.value,
                name=node.name,
                distance=node.distance,
                fan_in=node.fan_in,
                is_tested=node.is_tested,
                severity=node.severity.value,
                risk_score=node.risk_score,
            )
            for node in result.impacted_nodes
        ],
        total_affected=result.total_affected,
        high_severity_count=result.high_severity_count,
        medium_severity_count=result.medium_severity_count,
        low_severity_count=result.low_severity_count,
        narration=narration,
    )


def _resolve_and_analyze(
    repository_id: str,
    user_id: str,
    request: ImpactAnalysisRequest,
):
    nodes, edges = load_graph_for_repo(
        repository_id,
        user_id,
    )

    try:
        node_type = (
            NodeType(request.node_type)
            if request.node_type
            else None
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid node_type: {request.node_type}",
        ) from exc

    try:
        node = resolve_symbol(
            request.symbol_name,
            nodes,
            node_type=node_type,
        )

    except SymbolNotFoundError as exc:
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc

    except AmbiguousSymbolError as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "candidates": [
                    {
                        "node_id": candidate.id,
                        "name": candidate.name,
                        "type": candidate.type.value,
                        "file_path": candidate.file_path,
                    }
                    for candidate in exc.candidates
                ],
            },
        ) from exc

    result = analyze_impact(
        node.id,
        nodes,
        edges,
    )

    return result, nodes


@router.post(
    "",
    response_model=ImpactAnalysisResponse,
)
def get_impact(
    repository_id: str,
    request: ImpactAnalysisRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> ImpactAnalysisResponse:
    """
    Deterministic impact analysis.

    No AI model call or entitlement check is required.
    Repository ownership is still required.
    """

    result, _nodes = _resolve_and_analyze(
        repository_id,
        user.id,
        request,
    )

    return _to_response(result)


@router.post(
    "/narrate",
    response_model=ImpactAnalysisResponse,
)
async def get_impact_narrated(
    repository_id: str,
    request: ImpactAnalysisRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> ImpactAnalysisResponse:
    """
    Impact analysis with AI-generated explanation.

    Requires:
      - Supabase authentication.
      - Repository ownership.
      - Active trial or subscription.
    """

    try:
        service_tier = check_access(user.id)

    except AccessDeniedError as exc:
        code = (
            402
            if "expired" in exc.message.lower()
            else 403
        )

        raise HTTPException(
            status_code=code,
            detail=exc.message,
        ) from exc

    result, nodes = _resolve_and_analyze(
        repository_id,
        user.id,
        request,
    )

    # Ownership is checked before reading the repository record.
    storage_dir = get_storage_dir_or_404(
        repository_id,
        user.id,
    )

    record = load_record(storage_dir)

    source_context = ""

    if record is not None:
        source_context = build_impact_context(
            result,
            nodes,
            Path(record.local_path),
        )

    async def llm_call(prompt: str) -> str:
        router_result = await run_known_task(
            task_type="impact_narration",
            service_tier=service_tier,
            system=(
                "You are an expert software engineer "
                "explaining this to a developer."
            ),
            prompt=prompt,
            max_tokens=2048,
        )

        return router_result["final_text"]

    try:
        narration = await narrate_impact(
            result,
            llm_call,
            source_context=source_context,
        )

    except AllFamiliesFailedError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Structured analysis succeeded, but no model provider "
                "was available to narrate it."
            ),
        ) from exc

    return _to_response(
        result,
        narration=narration,
    )
