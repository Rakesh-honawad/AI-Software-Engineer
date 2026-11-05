"""
Dead-code analysis result contract.

Same pattern as contracts/analysis/impact_result.py: pure data, no
computation, no AI. Lives in contracts/ because it's read by multiple
layers (api/, and eventually workspace-ui) that shouldn't need to
import analysis/dead_code/'s internals just to know this shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from graph.schema.node_types import NodeType


@dataclass
class DeadCodeFinding:
    node_id: str
    node_type: NodeType
    name: str
    file_path: str | None

    # True if this node's name matches a common route/handler naming
    # convention (get_*, post_*, *_handler, on_*, etc.) — a heuristic
    # signal that this might actually be a live entry point the
    # current graph builder can't see (no decorator parsing yet, see
    # DeadCodeAnalysisResult.caveat). Consumers should visually soften
    # findings where this is True rather than presenting them with the
    # same confidence as an ordinary unreached private helper.
    possible_entry_point: bool = False

    # Raw incoming `calls` edge count, REGARDLESS of whether the
    # caller(s) are themselves reachable from a root. A nonzero fan_in
    # here means "dead code calling dead code" (a mutually-isolated
    # cluster), not "this is actually alive" — still correctly a
    # finding, but worth surfacing so it doesn't look like a traversal
    # bug when someone sees fan_in > 0 on a "dead" node.
    fan_in_from_other_dead_code: int = 0


@dataclass
class DeadCodeAnalysisResult:
    repository_id: str
    total_candidates: int              # Function/Class/Service/RepositoryLayer nodes considered
    total_reachable: int               # of those, how many a root could reach
    findings: list[DeadCodeFinding] = field(default_factory=list)

    # ALWAYS populated, not just on request — this is a real, load-
    # bearing limitation of the current graph builder (see
    # analysis/dead_code/dead_code_analysis.py's module docstring),
    # not an edge case. Any caller/UI surfacing `findings` should
    # surface this alongside it, every time, not just in docs.
    caveat: str = (
        "Entry points are detected from repository_profile's entry_points "
        "and a small conventional-name allowlist only — route decorators "
        "(e.g. @app.get(...)) are not yet parsed into the graph, so a "
        "function with zero incoming `calls` edges may actually be a live "
        "route handler, not dead code. Findings with possible_entry_point=True "
        "are especially likely to be false positives; verify before removing "
        "any finding, and treat this as a worth-reviewing list, not a "
        "safe-to-delete list."
    )