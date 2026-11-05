"""
Impact narration — the ONLY place the AI touches the impact-analysis
feature. Takes an already-computed ImpactAnalysisResult (from
analysis/impact/, which never calls AI) and turns it into plain English:
what will likely break, why, and what to check. Never generates code,
per the original design for this feature — hints and explanation only.

DEPENDENCY INJECTION, ON PURPOSE:
This module takes `llm_call: Callable[[str], Awaitable[str]]` as a
parameter rather than importing ai/router/ directly — see
ai/router/orchestrator.py for what gets passed in at the route layer.

PROMPT DESIGN NOTE:
The prompt explicitly tells the model NOT to write code and to only
explain/hint — this is enforced by instruction, not by any code-level
guard, since the model's actual output can't be constrained at the
Python level. If this becomes unreliable in practice, the fix is a
stronger system prompt or a post-hoc check, not a change to this
module's contract with analysis/impact/.

SOURCE CONTEXT (added this session, via ai/context_builder/impact_context.py):
build_narration_prompt() now accepts an optional `source_context` string —
real source code for the changed node + highest-risk impacted nodes,
built separately by context_builder so this module still never touches
the filesystem or the graph directly. Passing "" (the default) preserves
the exact previous behavior (names/relationships only) — this is
backward compatible, not a breaking change to this module's contract.
"""

from __future__ import annotations

from typing import Awaitable, Callable

from contracts.analysis.impact_result import ImpactAnalysisResult, ImpactedNode, Severity

NarrationCall = Callable[[str], Awaitable[str]]

# Cap how many impacted nodes get spelled out individually in the
# prompt — a change touching 200 nodes shouldn't blow the context
# budget; the summary counts still convey the full picture.
MAX_NODES_IN_PROMPT = 15


def _format_node_line(node: ImpactedNode) -> str:
    tested_note = "has test coverage" if node.is_tested else "NO test coverage"
    thing_word = "thing depends" if node.fan_in == 1 else "things depend"
    return (
        f"- {node.name} ({node.node_type.value}, {tested_note}, "
        f"{node.distance} step{'s' if node.distance != 1 else ''} away, "
        f"{node.fan_in} {thing_word} on it)"
    )


def build_narration_prompt(result: ImpactAnalysisResult, source_context: str = "") -> str:
    """Pure string construction — no model call, no side effects.
    Kept separate from narrate_impact() so the prompt itself is
    testable/inspectable without needing a model at all.

    source_context: optional real-source block from
    ai/context_builder/impact_context.py's build_impact_context().
    Empty string (default) = old behavior, names/relationships only.
    """
    context_prefix = f"{source_context}\n\n" if source_context else ""

    if result.total_affected == 0:
        return (
            f"{context_prefix}"
            f"A developer is about to change `{result.changed_node_name}`. "
            f"The dependency graph shows nothing else depends on it. "
            f"Write one short sentence confirming this looks safe to change, "
            f"in a plain, calm tone. Do not write any code."
        )

    by_severity = {Severity.HIGH: [], Severity.MEDIUM: [], Severity.LOW: []}
    for node in result.impacted_nodes:
        by_severity[node.severity].append(node)

    sections = []
    for severity in (Severity.HIGH, Severity.MEDIUM, Severity.LOW):
        nodes = by_severity[severity][:MAX_NODES_IN_PROMPT]
        if nodes:
            lines = "\n".join(_format_node_line(n) for n in nodes)
            sections.append(f"{severity.value.upper()} risk:\n{lines}")

    remaining = result.total_affected - sum(
        min(len(v), MAX_NODES_IN_PROMPT) for v in by_severity.values()
    )
    truncation_note = (
        f"\n(and {remaining} more not listed individually)" if remaining > 0 else ""
    )

    return (
        f"{context_prefix}"
        f"A developer is about to change `{result.changed_node_name}`. "
        f"Deterministic dependency-graph analysis found {result.total_affected} "
        f"affected item(s): {result.high_severity_count} high risk, "
        f"{result.medium_severity_count} medium risk, {result.low_severity_count} low risk."
        f"\n\n" + "\n\n".join(sections) + truncation_note +
        "\n\nWrite a short, plain-English explanation for the developer: "
        "what will likely break, why the high-risk items are riskier "
        "(missing test coverage and/or being close in the call chain), "
        "and what to check before making the change. "
        + ("Use the real source code above to judge whether each dependency is "
           "trivial or load-bearing, not just that a dependency exists. "
           if source_context else "")
        + "Do NOT write any code. Do NOT suggest a specific code fix — "
        "only explain and hint at what needs attention."
    )


async def narrate_impact(result: ImpactAnalysisResult, llm_call: NarrationCall,
                          source_context: str = "") -> str:
    """The entry point. Builds the prompt, sends it via the injected
    llm_call, returns the model's plain-English narration.
    """
    prompt = build_narration_prompt(result, source_context=source_context)
    return await llm_call(prompt)