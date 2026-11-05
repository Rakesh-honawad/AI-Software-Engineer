"""
Graph builder — Python, Phase 1, single-file scope.

Takes ExtractedFile (from parsing/symbol_extractor/) and produces
GraphNode/GraphEdge objects against the LOCKED schema in graph/schema/.
Every edge is validated with is_valid_edge() before being emitted —
this is what catches a resolution mistake (e.g. treating a class
instantiation as a function call) before it corrupts the graph, rather
than discovering it during impact analysis months later.

SCOPE — single file, same-file resolution only:
  - `calls` edges only resolve names to Functions in the SAME FILE.
  - `inherits` edges only resolve base classes defined in the SAME FILE.
  - `imports` edges are emitted but the imported module is not itself
    resolved to a File node yet — that requires knowing the full repo's
    file layout (module path -> file path mapping), which is a
    repo-level pass, not a single-file one. See build_file_graph's
    docstring for exactly what's deferred and why.

This scope is intentional, not an oversight: a working single-file
builder that's correct within its stated limits is worth more right
now than a multi-file builder with silent resolution guesses.

FIXED — class-instantiation edge gap:
Previously, `mgr = CacheManager(); mgr.get_stats()` produced a CALLS
edge from the caller to `get_stats`, but nothing pointing AT
`CacheManager` itself. Across the whole edge schema, the only edge
type that ever pointed at a class node was INHERITS (from a
subclass) — so any class with zero subclasses always showed
total_affected=0 in impact analysis, regardless of how many places
actually used it. Fixed by emitting a REFERENCES edge
(EdgeType.REFERENCES — "type/name reference without a call") from the
calling function to the instantiated class, alongside the existing
method-call edge. Scope-matched to this module's same-file-only
resolution. Also required adding EdgeType.REFERENCES to
DEPENDS_ON_EDGE_TYPES in graph/algorithms/impact/traversal.py — the
edge existing alone wasn't enough; impact analysis had to count it.

ADDED — module-level member references (TypeScript/JS routes):
`router.post('/x', emailController.handleInbound)` passes a handler as
an argument. The extractor records it as (base, member) in
ExtractedFile.module_level_member_refs; this module queues it as an
unresolved attribute call from the File node, and repo_builder.py's
cross-file pass (2d) resolves it through imports into a REFERENCES
edge File -> Function.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from graph.schema.edge_types import EdgeSource, EdgeType, GraphEdge
from graph.schema.graph_schema import is_valid_edge
from graph.schema.node_types import GraphNode, NodeSource, NodeType
from parsing.semantic.attribute_call_resolver import resolve_local_instantiation_call, resolve_self_call
from parsing.symbol_extractor.python_symbols import ExtractedFile


@dataclass
class BuildResult:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    # (source_node_id, unresolved_name) pairs — kept as pairs, not bare
    # names, so a later repo-level pass can find its way back to the
    # exact caller/child-class once the target is found in another file.
    unresolved_calls: list[tuple[str, str]] = field(default_factory=list)
    unresolved_bases: list[tuple[str, str]] = field(default_factory=list)
    # (caller_id, "base.method") pairs for attribute calls that
    # couldn't be resolved (e.g. base is a function parameter with no
    # known type, not `self` or a locally-instantiated variable).
    unresolved_attribute_calls: list[tuple[str, str]] = field(default_factory=list)
    # {variable_name: ClassName} for module-level singleton
    # assignments in THIS file (e.g. `cache_manager = CacheManager()`
    # at module scope). Passed through from ExtractedFile unchanged;
    # exists on BuildResult specifically so
    # graph/builder/repo_builder.py's cross-file attribute-call pass can
    # look up "does file X define a module-level singleton named Y, and
    # if so, what class is it" for every OTHER file that imports it.
    module_level_instantiations: dict[str, str] = field(default_factory=dict)
    unresolved_references: list[tuple[str, str]] = field(default_factory=list)


def _file_node_id(file_path: str) -> str:
    return f"file:{file_path}"


def _class_node_id(file_path: str, class_name: str) -> str:
    return f"class:{file_path}:{class_name}"


def _interface_node_id(file_path: str, interface_name: str) -> str:
    return f"interface:{file_path}:{interface_name}"


def _function_node_id(file_path: str, function_name: str, parent_class: str | None) -> str:
    if parent_class:
        return f"function:{file_path}:{parent_class}.{function_name}"
    return f"function:{file_path}:{function_name}"


def _add_edge_if_valid(
    result: BuildResult,
    from_node: GraphNode,
    to_node: GraphNode,
    edge_type: EdgeType,
) -> bool:
    """Returns True if the edge was valid and added, False if rejected."""
    if is_valid_edge(edge_type, from_node.type, to_node.type):
        result.edges.append(
            GraphEdge(
                from_id=from_node.id,
                to_id=to_node.id,
                type=edge_type,
                confidence=1.0,
                source=EdgeSource.AST,
            )
        )
        return True
    return False


def build_file_graph(file_path: str, extracted: ExtractedFile) -> BuildResult:
    """Builds the graph fragment for ONE file. Repo-level orchestration
    (calling this per file, then a second pass to resolve cross-file
    imports/calls once every file's fragment exists) is deliberately
    NOT this function's job — that belongs to repo_builder.py. This
    function's contract is: given one file's extracted symbols, return
    a schema-valid graph fragment for exactly that file.
    """
    result = BuildResult()
    result.module_level_instantiations = dict(extracted.module_level_instantiations)

    file_node = GraphNode(
        id=_file_node_id(file_path),
        type=NodeType.FILE,
        name=file_path,
        file_path=file_path,
        source=NodeSource.AST,
    )
    result.nodes.append(file_node)

    # --- classes, and File -contains-> Class -------------------------
    class_nodes: dict[str, GraphNode] = {}
    for extracted_class in extracted.classes:
        class_node = GraphNode(
            id=_class_node_id(file_path, extracted_class.name),
            type=NodeType.CLASS,
            name=extracted_class.name,
            file_path=file_path,
            line_range=extracted_class.line_range,
            source=NodeSource.AST,
        )
        result.nodes.append(class_node)
        class_nodes[extracted_class.name] = class_node
        _add_edge_if_valid(result, file_node, class_node, EdgeType.CONTAINS)

    # --- interfaces, and File -contains-> Interface ------------------
    interface_nodes: dict[str, GraphNode] = {}

    for extracted_interface in extracted.interfaces:
        interface_node = GraphNode(
            id=_interface_node_id(file_path, extracted_interface.name),
            type=NodeType.INTERFACE,
            name=extracted_interface.name,
            file_path=file_path,
            line_range=extracted_interface.line_range,
            source=NodeSource.AST,
            properties={
                "properties": extracted_interface.properties,
            },
        )
        result.nodes.append(interface_node)
        interface_nodes[extracted_interface.name] = interface_node
        _add_edge_if_valid(result, file_node, interface_node, EdgeType.CONTAINS)

    # --- inherits: Class -> Class, same-file bases only ---------------
    for extracted_class in extracted.classes:
        child_node = class_nodes[extracted_class.name]
        for base_name in extracted_class.base_class_names:
            base_node = class_nodes.get(base_name)
            if base_node is not None:
                _add_edge_if_valid(result, child_node, base_node, EdgeType.INHERITS)
            else:
                # Base class not defined in this file — likely imported.
                # Cross-file resolution is a repo-level concern, not this
                # function's. Recorded, not silently lost.
                result.unresolved_bases.append((child_node.id, base_name))

    # --- functions/methods, and containment ---------------------------
    function_nodes: dict[str, GraphNode] = {}   # keyed by plain name, top-level only
    for extracted_function in extracted.functions:
        function_node = GraphNode(
            id=_function_node_id(file_path, extracted_function.name, extracted_function.parent_class),
            type=NodeType.FUNCTION,
            name=extracted_function.name,
            file_path=file_path,
            line_range=extracted_function.line_range,
            source=NodeSource.AST,
            properties={"is_route_handler": True} if extracted_function.is_route_handler else {},
        )
        result.nodes.append(function_node)

        if extracted_function.parent_class:
            parent = class_nodes.get(extracted_function.parent_class)
            if parent is not None:
                _add_edge_if_valid(result, parent, function_node, EdgeType.CONTAINS)
        else:
            _add_edge_if_valid(result, file_node, function_node, EdgeType.CONTAINS)
            # Only top-level functions go in the by-name lookup for call
            # resolution: plain-identifier calls can only ever resolve to
            # a top-level function, since attribute syntax (self.x(),
            # obj.x()) is needed to call a method, and attribute calls
            # are handled separately below.
            function_nodes[extracted_function.name] = function_node

    # --- calls: Function -> Function, resolved against top-level functions only
    all_function_nodes_by_id = {n.id: n for n in result.nodes if n.type == NodeType.FUNCTION}
    for extracted_function in extracted.functions:
        caller_id = _function_node_id(file_path, extracted_function.name, extracted_function.parent_class)
        caller_node = all_function_nodes_by_id[caller_id]

        for called_name in extracted_function.called_names:
            target = function_nodes.get(called_name)
            if target is not None and target.id != caller_node.id:
                _add_edge_if_valid(result, caller_node, target, EdgeType.CALLS)
            else:
                # Either not found (builtin, imported function, or a
                # class instantiation like `UserRepository()`), or a
                # self-call (recursion — not modeled as an edge in
                # Phase 1). Recorded rather than silently dropped.
                result.unresolved_calls.append((caller_node.id, called_name))

    # --- attribute calls: self.foo() and obj.foo() -----------------------
    # See parsing/semantic/attribute_call_resolver.py for what's resolved
    # vs. deliberately left unresolved.
    class_bases: dict[str, list[str]] = {
        ec.name: ec.base_class_names for ec in extracted.classes
    }
    for extracted_function in extracted.functions:
        caller_id = _function_node_id(file_path, extracted_function.name, extracted_function.parent_class)
        caller_node = all_function_nodes_by_id[caller_id]

        for base_name, method_name in extracted_function.attribute_calls:
            target: GraphNode | None = None

            if base_name in ("self", "this") and extracted_function.parent_class:
                target = resolve_self_call(
                    extracted_function.parent_class, method_name, result.nodes, class_bases, file_path,
                )
            elif base_name in extracted_function.local_instantiations:
                instantiated_class_name = extracted_function.local_instantiations[base_name]
                target = resolve_local_instantiation_call(
                    instantiated_class_name, method_name, result.nodes, class_bases, file_path,
                )

                # Record that this function references/uses the
                # instantiated CLASS itself, not just the one method
                # called on it. Without this, a class only shows up as an
                # impact-analysis target if something subclasses it.
                # Same-file-only, matching this module's resolution scope.
                instantiated_class_node = class_nodes.get(instantiated_class_name)
                if instantiated_class_node is not None:
                    _add_edge_if_valid(result, caller_node, instantiated_class_node, EdgeType.REFERENCES)

            if target is not None and target.id != caller_node.id:
                _add_edge_if_valid(result, caller_node, target, EdgeType.CALLS)
            else:
                result.unresolved_attribute_calls.append((caller_node.id, f"{base_name}.{method_name}"))

    # --- references: JSX usage / registry values / type annotations,
    # same-file only. Checks classes and interfaces too (not just
    # top-level functions), since type-annotation references point at
    # classes far more often than functions.
    for extracted_function in extracted.functions:
        caller_id = _function_node_id(file_path, extracted_function.name, extracted_function.parent_class)
        caller_node = all_function_nodes_by_id[caller_id]
        for name in extracted_function.referenced_names:
            target = function_nodes.get(name) or class_nodes.get(name) or interface_nodes.get(name)
            if target is not None and target.id != caller_node.id:
                _add_edge_if_valid(result, caller_node, target, EdgeType.REFERENCES)
            else:
                result.unresolved_references.append((caller_node.id, name))

    # --- module-level references: File -> Function, same-file only ---
    for name in extracted.module_level_references:
        target = function_nodes.get(name)
        if target is not None:
            _add_edge_if_valid(result, file_node, target, EdgeType.REFERENCES)
        else:
            result.unresolved_references.append((file_node.id, name))

    # --- module-level member references: router.post(path, ctrl.handler) ---
    # Queued as unresolved attribute calls from the File node so
    # repo_builder.py's cross-file pass (2d) can resolve them through
    # imports. getattr keeps this safe for extractors (e.g. Python) that
    # never populate module_level_member_refs.
    for base_name, member_name in getattr(extracted, "module_level_member_refs", []):
        result.unresolved_attribute_calls.append(
            (file_node.id, f"{base_name}.{member_name}")
        )

    # --- imports: File -> File, target not resolved yet ----------------
    # Emitting these as nodes/edges is deferred: the schema's `imports`
    # edge is File->File, but we don't have a module-path -> file-path
    # map without scanning the whole repo. A single-file builder cannot
    # honestly produce this edge — recording the raw import info instead
    # so a repo-level pass can resolve it once it exists.

    return result