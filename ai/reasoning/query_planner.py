"""
ai/reasoning/query_planner.py

Closes the actual gap behind "the AI feels dumb": the frontend was
doing pure literal string matching (does "sign in" appear verbatim in
a file path?) with zero understanding that "sign in page" and
"login" mean the same thing, and zero concept of relational language
("connected to", "indirectly", "depends on") at all — those aren't
text-search problems, they're graph-traversal problems.

This module asks a fast/cheap model to translate a free-text question
into a QueryPlan: what to actually search for (expanded with likely
synonyms/related terms, not just the literal words typed), and
whether the question needs graph traversal (and in which direction)
on top of that search. repo_qa.py executes the plan against the REAL
graph — this module only decides what to look for, it never fabricates
an answer itself.

Same injection-of-llm_call pattern as every other narration/reasoning
module in this codebase; same heuristic-fallback discipline as
ai/router/classifier.py — a failed/unparseable model call degrades to
a naive plan (literal words as search terms, no traversal) rather than
blocking the whole request.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Literal

logger = logging.getLogger("ai.reasoning.query_planner")

PlannerCall = Callable[[str], Awaitable[str]]

TraversalKind = Literal["none", "impact", "reachability"]


@dataclass
class QueryPlan:
    search_terms: list[str]     # words/phrases to look for in node names/file paths — AI-expanded, not just literal input words
    wants_traversal: bool
    traversal_kind: TraversalKind
    reasoning: str               # short note on why, surfaced in logs/debugging, not shown to the end user


PLANNER_SYSTEM = (
    "You are a query planner for a codebase search tool. Given a developer's "
    "question about their repository, respond ONLY with JSON, no other text, "
    "in this exact shape:\n"
    '{"search_terms": ["term1", "term2"], "wants_traversal": false, '
    '"traversal_kind": "none", "reasoning": "short note"}\n\n'
    "search_terms: 2-6 short words/phrases likely to appear in the ACTUAL file "
    "names, class names, or function names for what the developer is asking "
    "about. Think like a developer naming things — expand vague/product "
    "language into likely code terms (e.g. \"sign in page\" -> [\"login\", "
    "\"signin\", \"auth\"], \"the chat feature\" -> [\"chat\", \"message\", "
    "\"conversation\"]). Include the literal words too, not just synonyms.\n\n"
    "wants_traversal: true if the question asks about a RELATIONSHIP between "
    "code (\"connected to\", \"depends on\", \"impact of changing\", "
    "\"indirectly\", \"what uses this\", \"what breaks if\") rather than just "
    "\"find/show me X\".\n\n"
    "traversal_kind: \"impact\" if asking what depends ON something / what "
    "breaks if changed; \"reachability\" if asking what something USES/CALLS "
    "downstream; \"none\" if wants_traversal is false."
)


def _heuristic_fallback(question: str) -> QueryPlan:
    """Naive backup if the planner model call fails/returns unparseable
    output — never blocks the whole request just because this one
    fast classification step had a bad day. Same posture as
    classifier.py's own heuristic fallback."""
    lowered = question.lower()
    words = [w for w in lowered.replace("?", " ").split() if len(w) > 2]
    wants_traversal = any(
        kw in lowered for kw in ("connect", "depend", "impact", "indirect", "use", "break", "affect")
    )
    traversal_kind: TraversalKind = "impact" if wants_traversal else "none"
    return QueryPlan(
        search_terms=words[:6],
        wants_traversal=wants_traversal,
        traversal_kind=traversal_kind,
        reasoning="heuristic fallback — planner model call failed or returned unparseable output",
    )


async def plan_query(question: str, llm_call: PlannerCall) -> QueryPlan:
    try:
        raw = await llm_call(f"{PLANNER_SYSTEM}\n\nQuestion: {question}")
        data = json.loads(raw.strip().strip("`").removeprefix("json").strip())
        kind = data.get("traversal_kind", "none")
        if kind not in ("none", "impact", "reachability"):
            kind = "none"
        return QueryPlan(
            search_terms=[str(t) for t in data.get("search_terms", [])][:8],
            wants_traversal=bool(data.get("wants_traversal", False)),
            traversal_kind=kind,  # type: ignore[arg-type]
            reasoning=str(data.get("reasoning", "")),
        )
    except Exception as e:  # noqa: BLE001 — any planner failure degrades, never propagates
        logger.warning("Query planner failed (%s), using heuristic fallback", e)
        return _heuristic_fallback(question)