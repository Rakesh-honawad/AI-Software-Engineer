"""
Dead-code analysis — domain interpretation on top of pure graph
reachability. Same layering as analysis/impact/impact_analysis.py:
this file never imports ai/ (enforced by convention, not tooling, same
as the rest of analysis/ per CLAUDE.md's non-negotiable rule #1).

READ THIS BEFORE TRUSTING THE OUTPUT: the current graph builder
(graph/builder/, per docs/schema/graph_builder_pipeline_v1.md) only
produces File/Class/Function nodes and contains/calls/inherits edges.
The richer schema types that WOULD make entry-point detection reliable
— Endpoint nodes, Service nodes, `exposes`/`tests` edges — exist in
graph/schema/{node_types,edge_types}.py but are NOT populated by
anything yet (that's frameworks/, still an empty Phase 2 stub). This
means route decorators (@app.get(...), @router.post(...)) are
completely invisible to this analysis. A function with zero incoming
`calls` edges might be a real, live, externally-invoked route handler
— not dead code. See ROOT DETECTION below for how this is mitigated
(not solved) via entry_points + naming heuristics, and
DeadCodeAnalysisResult.caveat, which is ALWAYS attached to every
result so this limitation travels with the data, not just the docs.
"""

from __future__ import annotations

from graph.algorithms.dependency.reachability import forward_reachability
from graph.schema.edge_types import EdgeType, GraphEdge
from graph.schema.node_types import GraphNode, NodeType
from contracts.analysis.dead_code_result import DeadCodeAnalysisResult, DeadCodeFinding

# Node types eligible to be flagged dead. Deliberately excludes:
# MODULE/FILE/REPOSITORY (structural containers, not executable units),
# CONFIGURATION/ENTITY/EXTERNAL_DEPENDENCY (not code that "runs"),
# INTERFACE (an unused interface is a different finding than dead code),
# FEATURE (an AI-synthesized grouping, not a real code unit),
# TEST (an unrun test is "unused test coverage", a different concern
# than dead production code).
CANDIDATE_TYPES = frozenset({
    NodeType.FUNCTION,
    NodeType.CLASS,
    NodeType.SERVICE,
    NodeType.REPOSITORY_LAYER,
})

# Function names conventionally used as process/CLI entry points, in
# addition to whatever repository_profile.entry_points resolves to a
# file for. Kept intentionally small and boring — this is a heuristic
# safety net, not a real entry-point detector.
CONVENTIONAL_ENTRY_NAMES = frozenset({"main", "__main__", "run"})

# DEFENSIVE FALLBACK — added after a real bug: repository_profile's
# Tier 1 entry-point detector (storage/repository_profile/
# tier1_generator.py) had a narrow hardcoded candidate list that
# missed a plain "app/main.py" layout, producing entry_points=[] and
# therefore ZERO roots / EVERY candidate flagged dead for a perfectly
# normal FastAPI repo. tier1_generator.py's detector was fixed
# directly, but this analysis doesn't fully trust that upstream data
# will always be complete for every repo/layout — if a candidate
# node's file is literally named one of these conventional entry
# filenames, its whole file is treated as a root REGARDLESS of what
# profile.entry_points contains. This only ever ADDS roots beyond
# what entry_points provides; it never removes or overrides anything.
CONVENTIONAL_ENTRY_FILENAMES = frozenset({
    "main.py", "app.py", "asgi.py", "wsgi.py", "manage.py", "server.py",
    "index.js", "index.ts", "server.js", "server.ts",
})

# Naming patterns that suggest a function might be an HTTP route
# handler or similar externally-invoked callback the current builder
# can't see via decorators. Used ONLY to set possible_entry_point on
# findings — never to exclude a node from candidacy, since a false
# negative here (missing a real dead function) is a much smaller
# problem than the analysis silently hiding a whole category of finding.
_ENTRY_POINT_NAME_HINTS = (
    "get_", "post_", "put_", "patch_", "delete_",
    "handle_", "handler", "_handler",
    "on_", "route_", "endpoint",
)


def _looks_like_possible_entry_point(name: str) -> bool:
    lowered = name.lower()
    return any(lowered.startswith(h) or lowered.endswith(h) for h in _ENTRY_POINT_NAME_HINTS)


def _file_basename(file_path: str | None) -> str:
    if not file_path:
        return ""
    return file_path.replace("\\", "/").rsplit("/", 1)[-1]


def _detect_roots(nodes: list[GraphNode], entry_point_file_paths: set[str]) -> set[str]:
    """ROOT DETECTION (v1, deliberately conservative):
    A Function/Class node is a root if ANY of:
      (a) its file_path matches one of repository_profile's
          entry_points (e.g. main.py, a CLI script, a Docker CMD
          target) — the whole file's top-level units are treated as
          roots rather than trying to guess which specific line
          actually runs first,
      (b) its name matches CONVENTIONAL_ENTRY_NAMES, or
      (c) its file's basename matches CONVENTIONAL_ENTRY_FILENAMES —
          the defensive fallback described above, independent of
          whether profile.entry_points caught this file or not.
    This intentionally does NOT try to detect API routes via
    decorators (can't — not parsed) or via naming alone (naming only
    sets possible_entry_point, doesn't grant root status — a function
    named get_user_profile is NOT assumed reachable just because of
    its name, only flagged as uncertain if it ends up unreached).
    """
    roots: set[str] = set()
    for node in nodes:
        # FILE nodes aren't candidates for dead-code findings, but an
        # entry-point file's own node CAN be a root for reachability
        # purposes — module-level registry patterns (e.g.
        # `const PANELS = { budget: BudgetPanel }`) emit a
        # File->Function REFERENCES edge (see python_builder.py's
        # module-level-references pass) that would otherwise be
        # permanently unreachable, since FILE is never a candidate
        # type and nothing else ever points at a File node.
        if node.type == NodeType.FILE and node.file_path in entry_point_file_paths:
            roots.add(node.id)
        if node.type not in CANDIDATE_TYPES:
            continue
        if node.file_path and node.file_path in entry_point_file_paths:
            roots.add(node.id)
        elif node.name in CONVENTIONAL_ENTRY_NAMES:
            roots.add(node.id)
        elif _file_basename(node.file_path) in CONVENTIONAL_ENTRY_FILENAMES:
            roots.add(node.id)
        elif node.properties.get("is_route_handler"):
            # Decorator-based route registration (@app.get(...) etc.)
            # isn't parsed into any edge — see python_symbols.py's
            # is_route_handler detection — so without this, every real
            # HTTP-invoked handler would falsely show up as dead code.
            roots.add(node.id)
    return roots


def analyze_dead_code(
    repository_id: str,
    nodes: list[GraphNode],
    edges: list[GraphEdge],
    entry_point_file_paths: list[str],
) -> DeadCodeAnalysisResult:
    """entry_point_file_paths should come from
    RepositoryProfile.entry_points (Tier 1, already deterministically
    detected — see repository_profile_schema_v1.md). Pass file paths
    as they appear in GraphNode.file_path (same convention used
    throughout, e.g. relative to repo root). Even if this list is
    incomplete or empty, CONVENTIONAL_ENTRY_FILENAMES above still
    provides a defensive baseline — see its comment for why that
    safety net exists.
    """
    live_nodes = [n for n in nodes if not n.is_deleted]
    candidates = [n for n in live_nodes if n.type in CANDIDATE_TYPES]

    entry_point_set = set(entry_point_file_paths)
    roots = _detect_roots(live_nodes, entry_point_set)

    reached = forward_reachability(roots, edges)

    # A reached class's own __init__ is always reached too — Python
    # invokes __init__ implicitly on instantiation (`ClassName()`),
    # so there's never a literal `.__init__(...)` call in source for
    # the extractor to see and turn into a CALLS edge. Without this,
    # every class only ever used via instantiation (the overwhelmingly
    # common case) would have its own constructor falsely flagged
    # dead, even though it necessarily runs whenever the class does.
    # CONTAINS (class -> its __init__) is deliberately excluded from
    # forward_reachability's walked edge types elsewhere (structural,
    # not usage) — this is a narrow, __init__-specific exception, not
    # a general "reachable class reaches all its methods" rule (that
    # would hide real dead methods on otherwise-live classes).
    init_by_class_id: dict[str, str] = {}
    for edge in edges:
        if edge.type != EdgeType.CONTAINS:
            continue
        target = next((n for n in live_nodes if n.id == edge.to_id), None)
        if target is not None and target.type == NodeType.FUNCTION and target.name == "__init__":
            init_by_class_id[edge.from_id] = edge.to_id

    for class_id, init_id in init_by_class_id.items():
        if class_id in reached:
            reached.add(init_id)

    # fan_in_from_other_dead_code: raw incoming `calls` count per node,
    # computed once up front rather than per-finding (O(edges) instead
    # of O(candidates * edges)).
    incoming_call_counts: dict[str, int] = {}
    for edge in edges:
        incoming_call_counts[edge.to_id] = incoming_call_counts.get(edge.to_id, 0) + 1

    findings: list[DeadCodeFinding] = []
    for node in candidates:
        if node.id in reached:
            continue
        findings.append(
            DeadCodeFinding(
                node_id=node.id,
                node_type=node.type,
                name=node.name,
                file_path=node.file_path,
                possible_entry_point=_looks_like_possible_entry_point(node.name),
                fan_in_from_other_dead_code=incoming_call_counts.get(node.id, 0),
            )
        )

    # Sort so the most-likely-genuine findings (not flagged as
    # possible entry points) surface first — reviewing this list top
    # to bottom should front-load the highest-confidence findings.
    findings.sort(key=lambda f: (f.possible_entry_point, f.file_path or "", f.name))

    total_reachable_candidates = sum(1 for n in candidates if n.id in reached)

    return DeadCodeAnalysisResult(
        repository_id=repository_id,
        total_candidates=len(candidates),
        total_reachable=total_reachable_candidates,
        findings=findings,
    )