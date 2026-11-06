"""
ai/narration/node_explain_narration.py

Fixes a real, confirmed bug: the frontend's "Explain X" chat command
was silently routing into impact_narration.py's prompt (via a regex
matching the word "explain"), which is DELIBERATELY a one-sentence,
safe-to-change-or-not verdict, never designed to actually explain
what code does.

This module is a distinct, general-purpose "explain this node" prompt:
what does this code do, what is its role, what should someone know
before touching it. Built on the pattern already proven in
dead_code_narration.py (real source plus structured questions).

Same injection pattern as impact_narration.py / dead_code_narration.py:
takes llm_call as a parameter, never imports ai/router/ directly.

v2: answer length now scales with how important the node is. Small,
little-used nodes get a 3-sentence answer; nodes with many dependents
or long source get a short structured answer.
"""

from __future__ import annotations

from typing import Awaitable, Callable

NarrationCall = Callable[[str], Awaitable[str]]

# Bounds prompt size even for a large file/class.
MAX_SOURCE_CHARS = 6000

# A node counts as "important" when enough other code depends on it, or when
# it is long enough that a one-line summary would hide real logic.
IMPORTANT_FAN_IN = 5
LONG_SOURCE_CHARS = 1500

# Three backticks, built here so they never sit literally inside a string.
FENCE = "`" * 3


def build_explain_node_prompt(
    node_name: str,
    node_type: str,
    file_path: str,
    source_text: str,
    fan_in: int | None = None,
) -> str:
    """
    Pure string construction: no model call, no side effects.

    Short nodes that little else depends on get a brief answer.
    Nodes with many dependents, or long source, get the fuller structure.
    """
    truncated = source_text[:MAX_SOURCE_CHARS]
    truncation_note = (
        f"\n[...truncated at {MAX_SOURCE_CHARS} characters...]"
        if len(source_text) > MAX_SOURCE_CHARS
        else ""
    )

    important = (fan_in or 0) >= IMPORTANT_FAN_IN or len(source_text) > LONG_SOURCE_CHARS

    header = (
        f"A developer is looking at `{node_name}` ({node_type}) in `{file_path}` "
        "and wants to understand what it does."
    )

    code_block = (
        "SOURCE CODE:\n"
        + FENCE
        + "\n"
        + truncated
        + truncation_note
        + "\n"
        + FENCE
    )

    if not important:
        return (
            header
            + "\n\n"
            + code_block
            + "\n\n"
            + "Explain it briefly: at most 3 short sentences, in plain terms, "
            "covering what it does and the key detail of how. No headings, "
            "no bullet points. Do not discuss impact, risk, callers, or what "
            "depends on it. Reference actual names from the code, and do not "
            "give a generic description."
        )

    dependency_line = ""
    if fan_in is not None and fan_in >= IMPORTANT_FAN_IN:
        dependency_line = (
            f"\nNote: {fan_in} other places depend on this. Mention that in "
            "one short sentence at the end, only if it helps the reader."
        )

    return (
        header
        + dependency_line
        + "\n\n"
        + code_block
        + "\n\n"
        + "Answer in this structure, and keep the whole answer under about "
        "150 words:\n"
        "1. PURPOSE: one sentence on what it does.\n"
        "2. HOW IT WORKS: 2-3 sentences on the real logic (branches, side "
        "effects, what it calls or reads/writes).\n"
        "3. WATCH OUT: include this only if there is a genuinely non-obvious "
        "behavior or edge case, as one sentence. Otherwise leave this "
        "section out entirely.\n\n"
        "Reference actual names from the code. Do not give a generic "
        "description."
    )


async def narrate_node_explanation(prompt: str, llm_call: NarrationCall) -> str:
    """Entry point: same shape as narrate_impact()/narrate_dead_code_finding()."""
    return await llm_call(prompt)