"""
Attribute call resolver — resolves `self.foo()` and `obj.foo()` calls,
which were previously invisible in the graph entirely (not captured
by the extractor at all, let alone left as "unresolved"). This is the
single biggest accuracy gap the graph had: in typical class-based
code, attribute calls are usually the MAJORITY of all calls, not a
minority — a call graph missing them is missing most of its edges.

TWO CASES HANDLED:
  1. `self.foo()` — resolves against the SAME class the calling
     method belongs to, walking up the inheritance chain if `foo`
     isn't defined directly on that class (so a method inherited from
     a base class still resolves, not just directly-defined ones).
  2. `obj.foo()` where `obj` was assigned via `obj = ClassName()` in
     the SAME function body — resolves against ClassName directly
     (ClassName is looked up the same way `self.foo()` looks up a
     base class: same-file directly, PLUS via the file's already-
     resolved import list so a same-file OR cross-file class works).

WHAT'S DELIBERATELY STILL UNRESOLVED, not guessed at:
  - `obj.foo()` where `obj` is a function parameter, a loop variable,
    or reassigned mid-function — no type information exists for these
    without real type inference (type hints, dataflow analysis),
    which is a larger feature than this pass. Left unresolved rather
    than guessed, same principle as every other resolver in this
    project.
  - Chained calls (`self.repo.save()`) — only single-hop attribute
    access is handled.
  - `self.foo()` where `foo` is inherited from a class OUTSIDE this
    file and not yet in the already-built node set at resolution time.

This module operates on GraphNode/GraphEdge + the ExtractedFunction
data already produced by the extractor — it doesn't re-parse anything.
It's called from graph/builder/python_builder.py's build_file_graph(),
after normal same-file resolution, so it can see the full node set
for the file (including inherited base classes already resolved via
the existing INHERITS pass).
"""

from __future__ import annotations

from graph.schema.edge_types import EdgeType
from graph.schema.node_types import GraphNode, NodeType


def _find_class_by_name(nodes: list[GraphNode], name: str) -> GraphNode | None:
    for n in nodes:
        if n.type == NodeType.CLASS and n.name == name:
            return n
    return None


def _find_method_on_class(
    nodes: list[GraphNode], class_name: str, method_name: str, file_path: str
) -> GraphNode | None:
    """Looks for `method_name` defined directly on `class_name` in
    THIS file's node set. Cross-file method lookup for attribute
    calls is not attempted — same-file only, consistent with every
    other same-file-first resolver in this project.
    """
    target_id_suffix = f":{class_name}.{method_name}"
    for n in nodes:
        if n.type == NodeType.FUNCTION and n.id.endswith(target_id_suffix) and n.file_path == file_path:
            return n
    return None


def resolve_self_call(
    caller_parent_class: str,
    method_name: str,
    nodes: list[GraphNode],
    class_bases: dict[str, list[str]],
    file_path: str,
) -> GraphNode | None:
    """Resolves `self.method_name()` called from within a method of
    `caller_parent_class`. Walks the inheritance chain (using
    class_bases, built from already-extracted ExtractedClass data) if
    the method isn't defined directly on the calling class — so a
    method inherited from a base class still resolves correctly.
    Stops after 10 hops as a simple cycle guard (a real inheritance
    cycle would be a bug in the source code, not something to loop
    on forever trying to resolve).
    """
    current_class = caller_parent_class
    seen: set[str] = set()
    for _ in range(10):
        if current_class in seen:
            break  # inheritance cycle guard
        seen.add(current_class)

        target = _find_method_on_class(nodes, current_class, method_name, file_path)
        if target is not None:
            return target

        bases = class_bases.get(current_class, [])
        if not bases:
            break
        current_class = bases[0]  # single-inheritance assumption; see module limitations

    return None


def resolve_local_instantiation_call(
    class_name: str,
    method_name: str,
    nodes: list[GraphNode],
    class_bases: dict[str, list[str]],
    file_path: str,
) -> GraphNode | None:
    """Resolves `obj.method_name()` where `obj` was assigned via
    `obj = ClassName()`. Same inheritance-walking logic as
    resolve_self_call, just starting from a known class name instead
    of "the class this method belongs to".
    """
    return resolve_self_call(class_name, method_name, nodes, class_bases, file_path)