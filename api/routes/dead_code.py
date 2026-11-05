
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from ai.narration.dead_code_narration import (
    build_dead_code_explain_prompt,
    narrate_dead_code_finding,
)
from ai.router.orchestrator import run_known_task
from ai.router.router_v2 import AllFamiliesFailedError
from analysis.dead_code.dead_code_analysis import analyze_dead_code
from api.dependencies.repository_deps import (
    get_storage_dir_or_404,
    load_graph_for_repo,
    load_profile_for_repo,
)
from api.schemas.dead_code_explain_schemas import (
    DeadCodeExplainRequest,
    DeadCodeExplainResponse,
)
from api.schemas.dead_code_schemas import (
    DeadCodeAnalysisResponse,
    DeadCodeFindingResponse,
)
from auth.dependencies import AuthenticatedUser, get_current_user
from entitlements.service import AccessDeniedError, check_access
from repository.manager.repository_manager import load_record

router = APIRouter(prefix="/repositories", tags=["dead-code"])


@router.get(
    "/{repository_id}/dead-code",
    response_model=DeadCodeAnalysisResponse,
)
def get_dead_code(
    repository_id: str,
    user: AuthenticatedUser = Depends(get_current_user),
) -> DeadCodeAnalysisResponse:
    """
    Structured dead-code analysis.

    Only the authenticated repository owner can access this analysis.
    No AI calls are made by this endpoint.
    """

    nodes, edges = load_graph_for_repo(
        repository_id,
        user.id,
    )

    profile = load_profile_for_repo(
        repository_id,
        user.id,
    )

    result = analyze_dead_code(
        repository_id=repository_id,
        nodes=nodes,
        edges=edges,
        entry_point_file_paths=profile.entry_points,
    )

    return DeadCodeAnalysisResponse(
        repository_id=result.repository_id,
        total_candidates=result.total_candidates,
        total_reachable=result.total_reachable,
        findings=[
            DeadCodeFindingResponse(
                node_id=finding.node_id,
                node_type=finding.node_type.value,
                name=finding.name,
                file_path=finding.file_path,
                possible_entry_point=finding.possible_entry_point,
                fan_in_from_other_dead_code=(
                    finding.fan_in_from_other_dead_code
                ),
            )
            for finding in result.findings
        ],
        caveat=result.caveat,
    )


@router.post(
    "/{repository_id}/dead-code/explain",
    response_model=DeadCodeExplainResponse,
)
async def explain_dead_code_finding(
    repository_id: str,
    request: DeadCodeExplainRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> DeadCodeExplainResponse:
    """
    Explain a structural dead-code finding using its actual source code.

    Requires:
      - A valid Supabase-authenticated user.
      - Ownership of the requested repository.
      - An active trial or subscription.
    """

    # Verify entitlement using the authenticated identity.
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

    # Resolve storage using the authenticated owner.
    storage_dir = get_storage_dir_or_404(
        repository_id,
        user.id,
    )

    record = load_record(storage_dir)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        )

    nodes, edges = load_graph_for_repo(
        repository_id,
        user.id,
    )

    profile = load_profile_for_repo(
        repository_id,
        user.id,
    )

    result = analyze_dead_code(
        repository_id=repository_id,
        nodes=nodes,
        edges=edges,
        entry_point_file_paths=profile.entry_points,
    )

    finding = next(
        (
            item
            for item in result.findings
            if item.node_id == request.node_id
        ),
        None,
    )

    if finding is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"'{request.node_id}' is not a current "
                "dead-code finding for this repository."
            ),
        )

    if not finding.file_path:
        raise HTTPException(
            status_code=400,
            detail="This finding has no file_path to read source from.",
        )

    # Resolve and validate the source path so a malformed graph path
    # cannot cause a read outside the indexed repository.
    repository_root = Path(record.local_path).resolve()
    source_path = (
        repository_root / finding.file_path
    ).resolve()

    try:
        source_path.relative_to(repository_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Finding source path is outside the repository.",
        ) from exc

    if not source_path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Finding source file does not exist.",
        )

    try:
        source_text = source_path.read_text(
            encoding="utf-8",
            errors="ignore",
        )
    except OSError as exc:
        raise HTTPException(
            status_code=404,
            detail="Could not read the finding source file.",
        ) from exc

    prompt = build_dead_code_explain_prompt(
        node_name=finding.name,
        node_type=finding.node_type.value,
        file_path=finding.file_path,
        source_text=source_text,
        possible_entry_point=finding.possible_entry_point,
        caveat=result.caveat,
    )

    async def llm_call(prompt_text: str) -> str:
        router_result = await run_known_task(
            task_type="dead_code_explain",
            service_tier=service_tier,
            system=(
                "You are an expert software engineer "
                "explaining this to a developer."
            ),
            prompt=prompt_text,
        )
        return router_result["final_text"]

    try:
        explanation = await narrate_dead_code_finding(
            prompt,
            llm_call,
        )
    except AllFamiliesFailedError as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                "Structural finding is still valid, but no AI model "
                "is available to explain it."
            ),
        ) from exc

    return DeadCodeExplainResponse(
        node_id=finding.node_id,
        name=finding.name,
        file_path=finding.file_path,
        explanation=explanation,
    )
