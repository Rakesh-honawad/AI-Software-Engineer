
"""
api/routes/repo_qa.py

The real "ask anything about the repo" endpoint. Two model calls per
question (plan, then synthesize) — both go through ai/router/ like
everything else, so they get the same fallback/tier/entitlements
treatment as every other AI feature in this codebase.

Repository access is restricted to the authenticated Supabase user.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ai.reasoning.query_planner import plan_query
from ai.reasoning.repo_qa import answer_repo_question
from ai.router.orchestrator import run_known_task
from ai.router.router_v2 import AllFamiliesFailedError
from api.dependencies.repository_deps import (
    get_storage_dir_or_404,
    load_graph_for_repo,
)
from auth.dependencies import AuthenticatedUser, get_current_user
from entitlements.service import AccessDeniedError, check_access
from repository.manager.repository_manager import load_record

router = APIRouter(prefix="/repositories", tags=["repo-qa"])


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    search_terms: list[str]
    matched_node_count: int


@router.post("/{repository_id}/ask", response_model=AskResponse)
async def ask_repo_question(
    repository_id: str,
    request: AskRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> AskResponse:
    """
    Real freeform Q&A: plans the search (AI expands synonyms/intent),
    matches real nodes, walks the real graph if the question is
    relational, pulls real source for top matches, then asks a second
    model call to write the answer grounded in all of that.

    Only the authenticated repository owner can access this endpoint.
    """
    try:
        service_tier = check_access(user.id)
    except AccessDeniedError as e:
        code = 402 if "expired" in e.message.lower() else 403
        raise HTTPException(status_code=code, detail=e.message) from e

    # Resolve storage and graph using the authenticated owner ID.
    storage_dir = get_storage_dir_or_404(repository_id, user.id)
    nodes, edges = load_graph_for_repo(repository_id, user.id)

    record = load_record(storage_dir)
    if record is None:
        raise HTTPException(
            status_code=404,
            detail=f"No record found for '{repository_id}'.",
        )

    # Defense in depth: ensure the loaded record belongs to this user.
    if record.owner_id != user.id:
        raise HTTPException(status_code=404, detail="Repository not found.")

    async def plan_llm_call(p: str) -> str:
        result = await run_known_task(
            task_type="repo_qa_plan",
            service_tier=service_tier,
            system="You are a precise query planner. Respond with JSON only.",
            prompt=p,
            max_tokens=300,
        )
        return result["final_text"]

    async def synthesize_llm_call(p: str) -> str:
        result = await run_known_task(
            task_type="repo_qa_synthesize",
            service_tier=service_tier,
            system="You are an expert software engineer answering questions about real code.",
            prompt=p,
        )
        return result["final_text"]

    try:
        plan = await plan_query(request.question, plan_llm_call)
        result = await answer_repo_question(
            request.question,
            plan,
            nodes,
            edges,
            record.local_path,
            synthesize_llm_call,
        )
    except AllFamiliesFailedError as e:
        raise HTTPException(
            status_code=503,
            detail=f"No AI model is currently available to answer this: {e}",
        ) from e

    return AskResponse(
        answer=result.answer,
        search_terms=plan.search_terms,
        matched_node_count=result.matched_node_count,
    )
