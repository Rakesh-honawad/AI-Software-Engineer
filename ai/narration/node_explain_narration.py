"""
ai/narration/node_explain_narration.py

Fixes a real, confirmed bug: the frontend's "Explain X" chat command
was silently routing into impact_narration.py's prompt (via a regex
matching the word "explain"), which is DELIBERATELY a one-sentence,
safe-to-change-or-not verdict — never designed to actually explain
what code does. That's why "Explain X" answers always sounded like a
hardcoded "no impact, safe to remove" response: the model was
correctly following an instruction that was never meant for this
question.

This module is a distinct, general-purpose "explain this node" prompt
— what does this code do, what's its role, what should someone know
before touching it — built on the exact pattern already proven in
dead_code_narration.py (real source + a few structured questions,
not a vague "explain this"), just generalized to any node (function,
class, file) instead of only dead-code candidates.

Same injection pattern as impact_narration.py / dead_code_narration.py:
takes llm_call as a parameter, never imports ai/router/ directly.
"""

from __future__ import annotations

from typing import Awaitable, Callable

NarrationCall = Callable[[str], Awaitable[str]]

# Same reasoning as dead_code_narration.py's MAX_SOURCE_CHARS — bounds
# prompt size even for a large file/class.
MAX_SOURCE_CHARS = 6000


def build_explain_node_prompt(
    node_name: str,
    node_type: str,
    file_path: str,
    source_text: str,
    fan_in: int | None = None,
) -> str:
    """
    Pure string construction — no model call, no side effects, testable
    without AI, same discipline as every other prompt-builder in this
    codebase.

    fan_in (how many other things depend on this, if already known
    from a prior impact-analysis call) is optional context, not the
    point of this prompt — this is about explaining the CODE, not
    re-answering "what's the impact", which is impact_narration.py's
    job and stays separate.
    """
    truncated = source_text[:MAX_SOURCE_CHARS]
    truncation_note = (
        f"\n[...truncated at {MAX_SOURCE_CHARS} characters...]"
        if len(source_text) > MAX_SOURCE_CHARS else ""
    )

    dependency_note = (
        f"\n(For reference: {fan_in} other place{'s' if fan_in != 1 else ''} in the "
        f"codebase currently depend{'s' if fan_in == 1 else ''} on this — but that's "
        f"not what you're being asked about here; explain the CODE itself.)"
        if fan_in is not None else ""
    )

    return f"""A developer is looking at `{node_name}` ({node_type}) in `{file_path}` and wants to understand what it actually does before working with it.{dependency_note}

SOURCE CODE:
```
{truncated}{truncation_note}
```

Answer in this structure:
1. PURPOSE: 1-2 sentences — what does this code do, in plain terms?
2. HOW IT WORKS: 2-4 sentences walking through the actual logic — not a restatement of the purpose, the real mechanics (branches, side effects, what it calls or reads/writes, error handling if any).
3. THINGS TO KNOW BEFORE CHANGING IT: any non-obvious behavior, assumptions the code makes, edge cases it handles (or doesn't), or patterns (decorators, dependency injection, dynamic dispatch) that aren't visible from the code's structure alone.

Be specific to THIS code — reference actual variable/function names, actual branches, actual logic. Do not give a generic description that could apply to any function of this shape."""


async def narrate_node_explanation(prompt: str, llm_call: NarrationCall) -> str:
    """Entry point — same shape as narrate_impact()/narrate_dead_code_finding()."""
    return await llm_call(prompt)