"""
Dead-code finding narration — same pattern as ai/narration/impact_narration.py:
pure prompt construction here, the actual model call is injected as
llm_call: Callable[[str], str] so this stays testable without a live
router and doesn't import ai/router/ directly.

WHY THIS EXISTS: analysis/dead_code/dead_code_analysis.py's verdict is
purely structural — "nothing in the parsed call graph reaches this
node." That's honest but incomplete: it can't see decorators, dependency
injection, dynamic dispatch/reflection, or a docstring/TODO explaining
a node is scaffolding for a planned feature. This module asks the AI
to read the ACTUAL SOURCE CODE (not just the structural fact) and
reason about those blind spots specifically — the same way a human
reviewer would before deleting anything.

HARD RULE, same spirit as impact_narration.py's "explain and hint only,
never write code": this NEVER tells the developer to delete something.
It gives a reasoned opinion (looks-dead / looks-live / unsure) with
WHY, and flags anything that hints at a planned/future feature. The
developer decides. Enforced by prompt wording only (see
impact_narration.py's own documented gap on this same limitation) —
not a code-level guard.
"""

from __future__ import annotations

MAX_SOURCE_CHARS = 6000  # keeps prompt size bounded even for a large file


def build_dead_code_explain_prompt(
    node_name: str,
    node_type: str,
    file_path: str,
    source_text: str,
    possible_entry_point: bool,
    caveat: str,
) -> str:
    truncated = source_text[:MAX_SOURCE_CHARS]
    truncation_note = (
        f"\n[...file truncated at {MAX_SOURCE_CHARS} characters...]" if len(source_text) > MAX_SOURCE_CHARS else ""
    )

    entry_point_note = (
        "\nNote: a naming heuristic already flagged this as a POSSIBLE entry point "
        "(e.g. it looks like it could be a route handler or accessor) — weigh this "
        "heavily toward 'looks-live' unless the code clearly contradicts it."
        if possible_entry_point
        else ""
    )

    return f"""You are reviewing a dead-code finding from a static analysis tool, for a developer deciding whether it's safe to remove.

STRUCTURAL FINDING: `{node_name}` ({node_type}) in `{file_path}` has ZERO incoming calls in the parsed dependency graph — no other code in the repository appears to call it directly.

KNOWN BLIND SPOT IN THIS ANALYSIS: {caveat}
{entry_point_note}

SOURCE CODE:
```
{truncated}{truncation_note}
```

Based on reading this code (not just the structural fact above), answer:
1. VERDICT: one of "looks-dead", "looks-live", or "unsure" — your honest read given what the blind spot above means for this specific code.
2. REASONING: 2-4 sentences explaining your verdict. Specifically consider: could this be invoked via a decorator, dependency injection (e.g. FastAPI's Depends()), dynamic dispatch (getattr, registries, event handlers), or a framework convention that a plain call-graph can't see?
3. FUTURE_IMPLICATIONS: if the code, its docstring, comments, or naming suggest it's scaffolding for a planned/in-progress feature (not truly abandoned), say so explicitly. If there's no such signal, say "No indication of planned future use."

Do NOT recommend deleting or keeping the code — only give your reasoned read. The developer makes the final call."""


async def narrate_dead_code_finding(prompt: str, llm_call) -> str:
    """llm_call: Callable[[str], str] — same injection pattern as
    narrate_impact() in impact_narration.py. Raises whatever llm_call
    raises (e.g. AllProvidersFailedError from ai/router/) — the API
    route layer is responsible for translating that to a 503, same as
    the existing /impact/narrate route does."""
    return await llm_call(prompt)