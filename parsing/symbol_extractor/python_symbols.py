"""
Symbol extractor — Python, Phase 1.

Walks a tree-sitter syntax tree and produces plain dataclasses describing
what's in the file: functions, classes, imports, and same-file calls.
This is the layer that knows Python syntax; graph/builder/ knows nothing
about tree-sitter or Python grammar, only these dataclasses — so a
second language means a new extractor emitting the same shapes, not
changes to the builder.

KNOWN LIMITATION (documented, not a bug): call resolution only handles
direct calls to a plain name defined in the SAME FILE — e.g. `login()`
calling `create_user()` where both are top-level or same-class functions.
Attribute calls (`self.foo()`, `obj.method()`) are NOT resolved in Phase 1
— that requires type resolution (knowing what `self`/`obj` actually is),
which is explicitly parsing/semantic/, a stubbed Phase 2 module. Skipping
these rather than guessing keeps every `calls` edge Phase 1 produces
fully deterministic — no heuristic-guessed edges pretending to be AST-certain.

MODULE-LEVEL SINGLETON INSTANTIATIONS — added this session:
Fixes a real, confirmed-live gap: `cache_manager = CacheManager()` at
module scope (the standard Python singleton pattern) was previously
invisible to this extractor entirely — only in-function assignments
(`_extract_attribute_calls_and_locals`, scoped to a function body) were
captured. Every file that imports and calls methods on such a singleton
(e.g. `cache_manager.get_stats()` in a completely different file) had
zero graph edges pointing at the singleton's class — meaning impact
analysis could never surface real usage of a class instantiated this
way, regardless of how many places actually used it. See
graph/builder/repo_builder.py's new cross-file attribute-call
resolution pass for how this data gets consumed.
"""

from __future__ import annotations

from tree_sitter import Node

from .extracted_types import ExtractedClass, ExtractedFile, ExtractedFunction, ExtractedImport


def _line_range(node: Node) -> tuple[int, int]:
    return (node.start_point[0] + 1, node.end_point[0] + 1)


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8")


def _find_child_by_type(node: Node, type_name: str) -> Node | None:
    for child in node.children:
        if child.type == type_name:
            return child
    return None


def _extract_called_names(body_node: Node, source: bytes) -> list[str]:
    """Finds direct calls like `foo(...)` inside a function body.
    Attribute calls (`self.foo(...)`) are handled separately by
    _extract_attribute_calls_and_locals — no longer silently skipped,
    see that function.
    """
    called: list[str] = []

    def walk(node: Node) -> None:
        if node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node is not None and func_node.type == "identifier":
                called.append(_node_text(func_node, source))
        for child in node.children:
            walk(child)

    walk(body_node)
    return called


def _extract_attribute_calls_and_locals(
    body_node: Node, source: bytes
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """Captures what this extractor used to skip entirely:
    - attribute_calls: (base_name, method_name) for `base.method()`
      calls — e.g. `self.validate()` -> ("self", "validate"),
      `repo.save()` -> ("repo", "save"). Feeds
      parsing/semantic/attribute_call_resolver.py.
    - local_instantiations: {variable_name: ClassName} for
      `var = ClassName()` assignments in this function body — lets
      the resolver know `repo` is a UserRepository, so `repo.save()`
      can resolve to UserRepository.save, not just bare `self.x()`.

    Deliberately simple: only the direct `name = ClassName(...)`
    pattern, not reassignment, conditional branches assigning
    different types, or attribute assignments (`self.repo = ...`).
    Same "get the common case right, don't guess the hard ones"
    principle as the rest of this project's extractors.
    """
    attribute_calls: list[tuple[str, str]] = []
    local_instantiations: dict[str, str] = {}

    def walk(node: Node) -> None:
        if node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node is not None and func_node.type == "attribute":
                obj_node = func_node.child_by_field_name("object")
                attr_node = func_node.child_by_field_name("attribute")
                if obj_node is not None and obj_node.type == "identifier" and attr_node is not None:
                    attribute_calls.append((_node_text(obj_node, source), _node_text(attr_node, source)))
        elif node.type == "assignment":
            left = node.child_by_field_name("left")
            right = node.child_by_field_name("right")
            if left is not None and left.type == "identifier" and right is not None and right.type == "call":
                right_func = right.child_by_field_name("function")
                if right_func is not None and right_func.type == "identifier":
                    local_instantiations[_node_text(left, source)] = _node_text(right_func, source)
        for child in node.children:
            walk(child)

    walk(body_node)
    return attribute_calls, local_instantiations


def _extract_module_level_instantiation(node: Node, source: bytes) -> tuple[str, str] | None:
    """Same detection logic as the local_instantiations branch of
    _extract_attribute_calls_and_locals above, but applied to a single
    top-level (module-scope) assignment node instead of walking a
    function body. Returns (variable_name, ClassName) or None if this
    assignment isn't a direct `name = ClassName(...)` pattern.

    Deliberately as narrow as its function-scope counterpart: only a
    direct identifier = call(identifier) pattern. A module-level
    `cache_manager = _build_cache_manager()` (factory function, not a
    direct class call) or `cache_manager: CacheManager = CacheManager()`
    (annotated assignment — different tree-sitter node type) are left
    unresolved rather than guessed at, matching this project's existing
    "get the common case right, don't guess the hard ones" principle.
    """
    if node.type != "assignment":
        return None
    left = node.child_by_field_name("left")
    right = node.child_by_field_name("right")
    if left is None or left.type != "identifier" or right is None or right.type != "call":
        return None
    right_func = right.child_by_field_name("function")
    if right_func is None or right_func.type != "identifier":
        return None
    return _node_text(left, source), _node_text(right_func, source)


_ROUTE_DECORATOR_METHODS = frozenset({
    "get", "post", "put", "patch", "delete", "options", "head", "websocket",
})


def _decorator_looks_like_route(decorator_node: Node, source: bytes) -> bool:
    """True if a decorator matches the `@app.get(...)` / `@router.post(...)`
    shape FastAPI (and similarly, Flask blueprints) use to register a
    route. Deliberately shape-only: doesn't resolve what the object is
    bound to, and doesn't extract path/method — see
    ExtractedFunction.is_route_handler's docstring for why this minimal
    signal is enough for its purpose.
    """
    for child in decorator_node.children:
        if child.type == "call":
            func_node = child.child_by_field_name("function")
            if func_node is not None and func_node.type == "attribute":
                attr_node = func_node.child_by_field_name("attribute")
                if attr_node is not None and _node_text(attr_node, source) in _ROUTE_DECORATOR_METHODS:
                    return True
    return False


def _extract_type_annotation_names(type_node: Node, source: bytes) -> list[str]:
    """Collects every identifier inside a type annotation node — e.g.
    `ExplainNodeRequest` -> ["ExplainNodeRequest"], or
    `Optional[UserRepository]` -> ["Optional", "UserRepository"].
    Deliberately permissive rather than trying to distinguish the
    "real" type from generic wrappers (Optional/list/dict/...): a
    wrapper name simply won't resolve to anything downstream and stays
    harmlessly unresolved, same "don't guess the hard cases, but don't
    silently drop the easy ones either" principle as the rest of this
    extractor. This is what lets `request: ExplainNodeRequest` and
    `-> ExplainNodeResponse` style FastAPI/Pydantic signatures produce
    a REFERENCES edge — previously invisible entirely, since these
    classes are never instantiated with a `ClassName()` call anywhere
    in the handler (FastAPI/Pydantic does that internally).
    """
    names: list[str] = []

    def walk(n: Node) -> None:
        if n.type == "identifier":
            names.append(_node_text(n, source))
        for child in n.children:
            walk(child)

    walk(type_node)
    return names


def _extract_function(node: Node, source: bytes, parent_class: str | None) -> ExtractedFunction:
    name_node = node.child_by_field_name("name")
    name = _node_text(name_node, source) if name_node else "<anonymous>"
    body_node = node.child_by_field_name("body")
    called_names = _extract_called_names(body_node, source) if body_node else []
    attribute_calls, local_instantiations = (
        _extract_attribute_calls_and_locals(body_node, source) if body_node else ([], {})
    )

    referenced_names: list[str] = []
    parameters_node = node.child_by_field_name("parameters")
    if parameters_node is not None:
        for param in parameters_node.children:
            if param.type in ("typed_parameter", "typed_default_parameter"):
                type_node = param.child_by_field_name("type")
                if type_node is not None:
                    referenced_names.extend(_extract_type_annotation_names(type_node, source))
    return_type_node = node.child_by_field_name("return_type")
    if return_type_node is not None:
        referenced_names.extend(_extract_type_annotation_names(return_type_node, source))

    return ExtractedFunction(
        name=name,
        line_range=_line_range(node),
        parent_class=parent_class,
        called_names=called_names,
        attribute_calls=attribute_calls,
        local_instantiations=local_instantiations,
        referenced_names=referenced_names,
    )


def _extract_class(node: Node, source: bytes) -> ExtractedClass:
    name_node = node.child_by_field_name("name")
    name = _node_text(name_node, source) if name_node else "<anonymous>"

    base_names: list[str] = []
    superclasses_node = node.child_by_field_name("superclasses")
    if superclasses_node is not None:
        for child in superclasses_node.children:
            if child.type == "identifier":
                base_names.append(_node_text(child, source))

    return ExtractedClass(name=name, line_range=_line_range(node), base_class_names=base_names)


def _extract_import(node: Node, source: bytes) -> list[ExtractedImport]:
    """Handles both `import x.y` and `from x.y import a, b`."""
    line = node.start_point[0] + 1
    results: list[ExtractedImport] = []

    if node.type == "import_statement":
        for child in node.children:
            if child.type == "dotted_name":
                results.append(ExtractedImport(module=_node_text(child, source), line=line))
            elif child.type == "aliased_import":
                dotted = _find_child_by_type(child, "dotted_name")
                if dotted is not None:
                    results.append(ExtractedImport(module=_node_text(dotted, source), line=line))

    elif node.type == "import_from_statement":
        module_node = node.child_by_field_name("module_name")
        module = _node_text(module_node, source) if module_node else ""
        names: list[str] = []
        for child in node.children:
            if child.type == "dotted_name" and child != module_node:
                names.append(_node_text(child, source))
            elif child.type == "aliased_import":
                dotted = _find_child_by_type(child, "dotted_name")
                if dotted is not None:
                    names.append(_node_text(dotted, source))
        results.append(ExtractedImport(module=module, imported_names=names, line=line))

    return results


def extract_symbols(root: Node | None, source: str) -> ExtractedFile:
    """Walks the whole file. Only descends into class bodies to find
    methods (one level of nesting) — deeply nested functions (closures)
    are out of scope for Phase 1 graph nodes; they're implementation
    detail of their enclosing function, not independently notable.
    """
    if root is None:
        return ExtractedFile()

    source_bytes = source.encode("utf-8")
    result = ExtractedFile()

    try:
        def walk(node: Node, inside_class: str | None) -> None:
            for child in node.children:
                if child.type == "function_definition":
                    result.functions.append(_extract_function(child, source_bytes, inside_class))
                elif child.type == "class_definition":
                    extracted_class = _extract_class(child, source_bytes)
                    result.classes.append(extracted_class)
                    body_node = child.child_by_field_name("body")
                    if body_node is not None:
                        walk(body_node, inside_class=extracted_class.name)
                elif child.type in ("import_statement", "import_from_statement"):
                    result.imports.extend(_extract_import(child, source_bytes))
                elif child.type == "assignment" and inside_class is None:
                    found = _extract_module_level_instantiation(child, source_bytes)
                    if found is not None:
                        var_name, class_name = found
                        result.module_level_instantiations[var_name] = class_name
                elif child.type == "decorated_definition":
                    is_route = any(
                        grandchild.type == "decorator" and _decorator_looks_like_route(grandchild, source_bytes)
                        for grandchild in child.children
                    )
                    for grandchild in child.children:
                        if grandchild.type == "function_definition":
                            extracted_fn = _extract_function(grandchild, source_bytes, inside_class)
                            extracted_fn.is_route_handler = is_route
                            result.functions.append(extracted_fn)
                        elif grandchild.type == "class_definition":
                            extracted_class = _extract_class(grandchild, source_bytes)
                            result.classes.append(extracted_class)
                            body_node = grandchild.child_by_field_name("body")
                            if body_node is not None:
                                walk(body_node, inside_class=extracted_class.name)
                else:
                    walk(child, inside_class)

        walk(root, inside_class=None)
    except Exception as e:
        print(f"[extract-debug] parse/extract failed: {type(e).__name__}: {e}")
        return ExtractedFile()

    return result