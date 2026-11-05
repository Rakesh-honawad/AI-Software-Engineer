"""
ai/reasoning/repo_qa.py

The actual "ask anything about the repo" capability — what turns
plan_query()'s intent into a real, grounded answer:

  1. Match QueryPlan.search_terms against REAL node names/file paths
     (generous OR-matching here is correct and safe — this is candidate
     generation for the synthesis model to reason over, not the final
     answer; false positives get filtered out by the synthesis step
     when it writes the actual response, same way a human skims search
     results before answering).
  2. If the plan calls for it, walk the REAL graph — reverse_impact_traversal
     for "what depends on this" questions, forward_reachability for
     "what does this use" questions. Reuses the exact same, already-
     proven traversal functions impact analysis uses; no new graph
     logic invented here.
  3. Pull real source for the top few matched nodes (same bounded,
     truncated approach as ai/context_builder/impact_context.py).
  4. Ask a second model call to write the actual answer, grounded in
     everything gathered above — never asked to invent facts about
     the repo, only to explain/organize real ones.

HARD RULE, same discipline as narration modules: this module doesn't
call ai/router/ directly — llm_call is injected by the caller (the
API route), same pattern throughout this codebase.
"""

from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable

from ai.reasoning.query_planner import QueryPlan
from graph.algorithms.impact.traversal import reverse_impact_traversal
from graph.algorithms.dependency.reachability import forward_reachability
from graph.schema.node_types import GraphNode
from graph.schema.edge_types import GraphEdge

SynthesizeCall = Callable[[str], Awaitable[str]]

MAX_CANDIDATE_NODES = 12       # cap how many matched nodes we even consider
MAX_SOURCE_NODES = 5           # of those, how many get real source pulled
MAX_CHARS_PER_NODE = 1200
# The single best-matched candidate is usually the actual answer to
# the question — give it a much bigger budget than the rest, instead
# of truncating it at the same small cap as everything else. Real gap
# found live: a question about a ~250-line component got cut off
# mid-file, and the model had to honestly say it couldn't see the
# rest — correct behavior, but avoidable by just giving the top match
# more room, since it's the file the user almost always cares about.
MAX_CHARS_TOP_MATCH = 6000
MAX_RELATED_IN_PROMPT = 20     # cap traversal results listed, for token sanity

SYNTHESIS_SYSTEM = (
    "You are an expert software engineer answering a developer's question "
    "about their own codebase. Use ONLY the real data provided below — real "
    "node names, real relationships, real source code. If the data doesn't "
    "actually answer the question, say what you found and what's missing, "
    "rather than guessing or inventing file names/behavior that isn't shown "
    "to you. Be specific and concrete — reference actual names, not generic "
    "descriptions."
)


def _match_candidates(search_terms: list[str], nodes: list[GraphNode]) -> list[GraphNode]:
    if not search_terms:
        return []
    terms = [t.lower() for t in search_terms]
    scored: list[tuple[int, GraphNode]] = []
    for n in nodes:
        haystack = f"{n.name} {n.file_path or ''}".lower()
        score = sum(1 for t in terms if t in haystack)
        if score > 0:
            scored.append((score, n))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [n for _, n in scored[:MAX_CANDIDATE_NODES]]


def _read_snippet(local_path: str, file_path: str, max_chars: int) -> str | None:
    try:
        text = (Path(local_path) / file_path).read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    return text[:max_chars] + ("\n[...truncated...]" if len(text) > max_chars else "")


def _run_traversal(plan: QueryPlan, candidates: list[GraphNode], edges: list[GraphEdge]) -> dict[str, dict[str, int]]:
    """Returns {root_node_id: {related_node_id: distance}} for each
    candidate node, using whichever traversal direction the plan asked
    for. Empty dict if the plan doesn't call for traversal."""
    if not plan.wants_traversal or plan.traversal_kind == "none":
        return {}

    results: dict[str, dict[str, int]] = {}
    for node in candidates:
        if plan.traversal_kind == "impact":
            distances = reverse_impact_traversal(node.id, edges)
        else:  # "reachability"
            reached = forward_reachability({node.id}, edges)
            distances = {nid: 1 for nid in reached if nid != node.id}  # forward_reachability doesn't return distances, flatten to "reachable"
        if distances:
            results[node.id] = distances
    return results


def _build_context_block(
    plan: QueryPlan,
    candidates: list[GraphNode],
    traversal_results: dict[str, dict[str, int]],
    node_by_id: dict[str, GraphNode],
    local_path: str,
) -> str:
    sections: list[str] = []

    sections.append(
        "MATCHED NODES (real nodes in the repository matching the question's search terms "
        f"{plan.search_terms}):\n" +
        "\n".join(f"- {n.name} ({n.type.value}) — {n.file_path}" for n in candidates)
        if candidates else
        f"No nodes in the repository matched search terms {plan.search_terms}."
    )

    if traversal_results:
        kind_label = "depend on" if plan.traversal_kind == "impact" else "are used by"
        rel_lines = []
        for root_id, distances in traversal_results.items():
            root = node_by_id.get(root_id)
            if root is None:
                continue
            related = sorted(distances.items(), key=lambda kv: kv[1])[:MAX_RELATED_IN_PROMPT]
            if not related:
                continue
            names = ", ".join(
                f"{node_by_id[nid].name} (distance {d})" for nid, d in related if nid in node_by_id
            )
            rel_lines.append(f"Things that {kind_label} {root.name}: {names}")
        if rel_lines:
            sections.append("REAL GRAPH RELATIONSHIPS:\n" + "\n".join(rel_lines))
        else:
            sections.append("REAL GRAPH RELATIONSHIPS: none found — these nodes have no tracked dependents/dependencies.")

    source_sections = []
    for i, n in enumerate(candidates[:MAX_SOURCE_NODES]):
        if not n.file_path:
            continue
        char_budget = MAX_CHARS_TOP_MATCH if i == 0 else MAX_CHARS_PER_NODE
        snippet = _read_snippet(local_path, n.file_path, char_budget)
        if snippet:
            source_sections.append(f"--- {n.name} ({n.file_path}) ---\n{snippet}")
    if source_sections:
        sections.append("REAL SOURCE CODE:\n" + "\n\n".join(source_sections))

    return "\n\n".join(sections)


from dataclasses import dataclass


@dataclass
class RepoQAResult:
    answer: str
    matched_node_count: int


async def answer_repo_question(
    question: str,
    plan: QueryPlan,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    local_path: str,
    synthesize_llm_call: SynthesizeCall,
) -> RepoQAResult:
    """
    The entry point. `plan` is the already-computed QueryPlan (see
    query_planner.py) — kept as a separate parameter rather than
    computed here so the planning call and the synthesis call can use
    different models/tiers/system prompts at the API route layer,
    same separation as every other two-step flow in this codebase.
    """
    node_by_id = {n.id: n for n in nodes}
    candidates = _match_candidates(plan.search_terms, nodes)
    traversal_results = _run_traversal(plan, candidates, edges)
    context = _build_context_block(plan, candidates, traversal_results, node_by_id, local_path)

    prompt = f"{SYNTHESIS_SYSTEM}\n\nDeveloper's question: {question}\n\n{context}\n\nAnswer the question using the real data above."
    answer = await synthesize_llm_call(prompt)
    return RepoQAResult(answer=answer, matched_node_count=len(candidates))