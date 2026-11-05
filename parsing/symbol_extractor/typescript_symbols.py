"""
Symbol extractor — TypeScript/TSX/JavaScript.

Same contract as python_symbols.py: walks a tree-sitter tree, produces
ExtractedFile. graph/builder/ consumes this shape without caring which
language produced it.

FUNCTION FORMS HANDLED:
  function foo() {}                    -> top-level function
  const foo = () => {}                 -> top-level function
  class X { method() {} }              -> method, parent_class="X"

Also extracts TypeScript interface declarations and type references.

CALL RESOLUTION: only plain identifier calls (`foo()`), never
member/attribute calls (`this.foo()`, `obj.foo()`).

IMPORTS: named and default imports are captured.
Namespace imports are deliberately not captured.
"""

from __future__ import annotations

from tree_sitter import Node

from .extracted_types import (
    ExtractedClass,
    ExtractedFile,
    ExtractedFunction,
    ExtractedImport,
    ExtractedInterface,
)


def _line_range(node: Node) -> tuple[int, int]:
    return (node.start_point[0] + 1, node.end_point[0] + 1)


def _node_text(node: Node, source: bytes) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8")


def _extract_called_names(body_node: Node, source: bytes) -> list[str]:
    called: list[str] = []

    def walk(node: Node) -> None:
        if node.type == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node is not None and func_node.type == "identifier":
                called.append(_node_text(func_node, source))

        for child in node.children:
            walk(child)

    walk(body_node)
    return called


def _extract_referenced_names(body_node: Node, source: bytes) -> list[str]:
    """
    Names referenced as values, not called.

    Handles JSX usage:
        <Foo />

    And object-literal registry values:
        { home: HomePage }
    """
    referenced: list[str] = []

    def _jsx_tag_name(tag_container: Node) -> str | None:
        for child in tag_container.children:
            if child.type == "identifier":
                return _node_text(child, source)

            if child.type == "member_expression":
                obj_node = child.child_by_field_name("object")
                if obj_node is not None and obj_node.type == "identifier":
                    return _node_text(obj_node, source)

        return None

    def walk(node: Node) -> None:
        if node.type in ("jsx_self_closing_element", "jsx_opening_element"):
            name = _jsx_tag_name(node)

            if name is not None and name[0].isupper():
                referenced.append(name)

        elif node.type == "pair":
            value_node = node.child_by_field_name("value")

            if value_node is not None and value_node.type == "identifier":
                referenced.append(_node_text(value_node, source))

        for child in node.children:
            walk(child)

    walk(body_node)
    return referenced


def _extract_type_references(node: Node, source: bytes) -> list[str]:
    """
    Extract custom TypeScript type names from a function or declaration.

    Examples:
        function login(user: User): void {}
        const state = useState<User | null>(null)
        const value = data as User

    Tree-sitter represents custom TypeScript types as type_identifier.
    Built-in types such as string, number and void are not collected.
    """
    references: list[str] = []

    def walk(current: Node) -> None:
        if current.type == "type_identifier":
            references.append(_node_text(current, source))

        for child in current.children:
            walk(child)

    walk(node)

    # Preserve order while removing duplicates.
    return list(dict.fromkeys(references))


def _extract_module_level_references(
    object_node: Node,
    source: bytes,
) -> list[str]:
    """
    Extract references from a top-level object literal, for example:

        const routes = { home: HomePage }
    """
    referenced: list[str] = []

    def walk(node: Node) -> None:
        if node.type == "pair":
            value_node = node.child_by_field_name("value")

            if value_node is not None and value_node.type == "identifier":
                referenced.append(_node_text(value_node, source))

        for child in node.children:
            walk(child)

    walk(object_node)
    return referenced


def _find_wrapped_function(node: Node) -> Node | None:
    """
    Unwrap HOC/forwardRef patterns:

        connect(x)(function Inner() {})
        React.forwardRef((props, ref) => {...})
    """
    if node.type in ("arrow_function", "function_expression"):
        return node

    if node.type == "call_expression":
        args_node = node.child_by_field_name("arguments")

        if args_node is not None:
            for child in args_node.children:
                found = _find_wrapped_function(child)

                if found is not None:
                    return found

    return None


def _extract_attribute_calls_and_locals(
    body_node: Node,
    source: bytes,
) -> tuple[list[tuple[str, str]], dict[str, str]]:
    """
    Extract member calls and direct local instantiations.

    Examples:
        this.save()              -> ("this", "save")
        user.save()              -> ("user", "save")
        const x = new User()     -> {"x": "User"}
    """
    attribute_calls: list[tuple[str, str]] = []
    local_instantiations: dict[str, str] = {}

    def walk(node: Node) -> None:
        if node.type == "call_expression":
            func_node = node.child_by_field_name("function")

            if func_node is not None and func_node.type == "member_expression":
                obj_node = func_node.child_by_field_name("object")
                prop_node = func_node.child_by_field_name("property")

                if obj_node is not None and prop_node is not None:
                    if obj_node.type == "this":
                        attribute_calls.append(
                            ("this", _node_text(prop_node, source))
                        )

                    elif obj_node.type == "identifier":
                        attribute_calls.append(
                            (
                                _node_text(obj_node, source),
                                _node_text(prop_node, source),
                            )
                        )

        elif node.type == "variable_declarator":
            name_node = node.child_by_field_name("name")
            value_node = node.child_by_field_name("value")

            if (
                name_node is not None
                and name_node.type == "identifier"
                and value_node is not None
                and value_node.type == "new_expression"
            ):
                ctor_node = value_node.child_by_field_name("constructor")

                if ctor_node is not None and ctor_node.type == "identifier":
                    local_instantiations[
                        _node_text(name_node, source)
                    ] = _node_text(ctor_node, source)

        for child in node.children:
            walk(child)

    walk(body_node)

    return attribute_calls, local_instantiations


def _extract_class(node: Node, source: bytes) -> ExtractedClass:
    name_node = node.child_by_field_name("name")
    name = _node_text(name_node, source) if name_node else "<anonymous>"

    base_names: list[str] = []

    for child in node.children:
        if child.type == "class_heritage":
            for heritage_child in child.children:
                if heritage_child.type == "extends_clause":
                    for c in heritage_child.children:
                        if c.type == "identifier":
                            base_names.append(_node_text(c, source))

    return ExtractedClass(
        name=name,
        line_range=_line_range(node),
        base_class_names=base_names,
    )


def _extract_interface(
    node: Node,
    source: bytes,
) -> ExtractedInterface | None:
    name_node = node.child_by_field_name("name")

    if name_node is None:
        return None

    properties: list[dict[str, str | bool | None]] = []

    body_types = {"interface_body", "object_type"}

    for child in node.children:
        if child.type not in body_types:
            continue

        for member in child.children:
            if member.type != "property_signature":
                continue

            property_name = member.child_by_field_name("name")
            property_type = member.child_by_field_name("type")

            if property_name is None:
                continue

            type_text = (
                _node_text(property_type, source).lstrip(": ").strip()
                if property_type is not None
                else None
            )

            properties.append({
                "name": _node_text(property_name, source),
                "type": type_text,
                "optional": any(c.type == "?" for c in member.children),
            })

    return ExtractedInterface(
        name=_node_text(name_node, source),
        line_range=_line_range(node),
        properties=properties,
    )


def _extract_method(
    node: Node,
    source: bytes,
    parent_class: str,
) -> ExtractedFunction | None:
    name_node = node.child_by_field_name("name")

    if name_node is None:
        return None

    body_node = node.child_by_field_name("body")

    called_names = (
        _extract_called_names(body_node, source)
        if body_node
        else []
    )

    attribute_calls, local_instantiations = (
        _extract_attribute_calls_and_locals(body_node, source)
        if body_node
        else ([], {})
    )

    referenced_names = (
        _extract_referenced_names(body_node, source)
        if body_node
        else []
    )

    # Include custom types used in method parameters, return types,
    # generic arguments and type assertions.
    referenced_names.extend(
        _extract_type_references(node, source)
    )

    referenced_names = list(dict.fromkeys(referenced_names))

    return ExtractedFunction(
        name=_node_text(name_node, source),
        line_range=_line_range(node),
        parent_class=parent_class,
        called_names=called_names,
        attribute_calls=attribute_calls,
        local_instantiations=local_instantiations,
        referenced_names=referenced_names,
    )


def _extract_function_declaration(
    node: Node,
    source: bytes,
) -> ExtractedFunction | None:
    name_node = node.child_by_field_name("name")

    if name_node is None:
        return None

    body_node = node.child_by_field_name("body")

    called_names = (
        _extract_called_names(body_node, source)
        if body_node
        else []
    )

    attribute_calls, local_instantiations = (
        _extract_attribute_calls_and_locals(body_node, source)
        if body_node
        else ([], {})
    )

    referenced_names = (
        _extract_referenced_names(body_node, source)
        if body_node
        else []
    )

    # Include custom types used in the function signature and body.
    referenced_names.extend(
        _extract_type_references(node, source)
    )

    referenced_names = list(dict.fromkeys(referenced_names))

    return ExtractedFunction(
        name=_node_text(name_node, source),
        line_range=_line_range(node),
        parent_class=None,
        called_names=called_names,
        attribute_calls=attribute_calls,
        local_instantiations=local_instantiations,
        referenced_names=referenced_names,
    )


def _extract_arrow_const(
    node: Node,
    source: bytes,
) -> ExtractedFunction | None:
    """
    Handles:

        const foo = () => {...}

    Also handles HOC/forwardRef-wrapped definitions.

    Scans the complete variable declarator for type references so
    annotations outside the arrow function are included as well.
    """
    for declarator in node.children:
        if declarator.type != "variable_declarator":
            continue

        name_node = declarator.child_by_field_name("name")
        value_node = declarator.child_by_field_name("value")

        if name_node is None or value_node is None:
            continue

        inner_fn = _find_wrapped_function(value_node)

        if inner_fn is None:
            continue

        body_node = inner_fn.child_by_field_name("body")

        called_names = (
            _extract_called_names(body_node, source)
            if body_node
            else []
        )

        attribute_calls, local_instantiations = (
            _extract_attribute_calls_and_locals(body_node, source)
            if body_node
            else ([], {})
        )

        referenced_names = (
            _extract_referenced_names(body_node, source)
            if body_node
            else []
        )

        # Include function signature, generic and variable annotation types.
        referenced_names.extend(
            _extract_type_references(declarator, source)
        )

        referenced_names = list(dict.fromkeys(referenced_names))

        return ExtractedFunction(
            name=_node_text(name_node, source),
            line_range=_line_range(node),
            parent_class=None,
            called_names=called_names,
            attribute_calls=attribute_calls,
            local_instantiations=local_instantiations,
            referenced_names=referenced_names,
        )

    return None

def _build_function(
    name: str,
    range_node: Node,
    fn_node: Node,
    source: bytes,
) -> ExtractedFunction:
    body_node = fn_node.child_by_field_name("body")

    called_names = _extract_called_names(body_node, source) if body_node else []
    attribute_calls, local_instantiations = (
        _extract_attribute_calls_and_locals(body_node, source)
        if body_node
        else ([], {})
    )
    referenced_names = (
        _extract_referenced_names(body_node, source) if body_node else []
    )
    referenced_names.extend(_extract_type_references(fn_node, source))
    referenced_names = list(dict.fromkeys(referenced_names))

    return ExtractedFunction(
        name=name,
        line_range=_line_range(range_node),
        parent_class=None,
        called_names=called_names,
        attribute_calls=attribute_calls,
        local_instantiations=local_instantiations,
        referenced_names=referenced_names,
    )


def _extract_object_literal_functions(
    object_node: Node,
    source: bytes,
) -> list[ExtractedFunction]:
    """
    Functions defined inside an object literal:

        export const emailService = {
          async send() {...},          // method_definition
          other: () => {...},          // pair with function value
        }
    """
    found: list[ExtractedFunction] = []

    for member in object_node.children:
        if member.type == "method_definition":
            name_node = member.child_by_field_name("name")
            if name_node is None:
                continue
            found.append(
                _build_function(_node_text(name_node, source), member, member, source)
            )

        elif member.type == "pair":
            key_node = member.child_by_field_name("key")
            value_node = member.child_by_field_name("value")
            if key_node is None or value_node is None:
                continue
            fn_node = _find_wrapped_function(value_node)
            if fn_node is None:
                continue
            name = _node_text(key_node, source).strip("'\"")
            found.append(_build_function(name, member, fn_node, source))

    return found

def _extract_import(
    node: Node,
    source: bytes,
) -> ExtractedImport | None:
    source_node = node.child_by_field_name("source")

    if source_node is None:
        return None

    module = _node_text(source_node, source).strip("'\"")
    line = node.start_point[0] + 1

    imported_names: list[str] = []

    for child in node.children:
        if child.type != "import_clause":
            continue

        for clause_child in child.children:
            if clause_child.type == "identifier":
                # Default import: import Foo from "./mod"
                imported_names.append(_node_text(clause_child, source))

            elif clause_child.type == "named_imports":
                for spec in clause_child.children:
                    if spec.type == "import_specifier":
                        name_field = spec.child_by_field_name("name")

                        if name_field is not None:
                            imported_names.append(
                                _node_text(name_field, source)
                            )

            # Namespace imports deliberately remain out of scope.

    return ExtractedImport(
        module=module,
        imported_names=imported_names,
        line=line,
    )

def _collect_call_argument_refs(
    call_node: Node,
    source: bytes,
    result: ExtractedFile,
) -> None:
    """
    Handlers passed as call arguments, outside any function:

        router.get('/x', listRfps)                  -> module_level_references
        router.post('/x', ctrl.handleInbound)       -> module_level_member_refs
    """
    args_node = call_node.child_by_field_name("arguments")
    if args_node is None:
        return

    for arg in args_node.children:
        if arg.type == "identifier":
            result.module_level_references.append(_node_text(arg, source))
        elif arg.type == "member_expression":
            obj_node = arg.child_by_field_name("object")
            prop_node = arg.child_by_field_name("property")
            if (
                obj_node is not None
                and prop_node is not None
                and obj_node.type == "identifier"
            ):
                result.module_level_member_refs.append(
                    (_node_text(obj_node, source), _node_text(prop_node, source))
                )

def extract_symbols(
    root: Node | None,
    source: str,
) -> ExtractedFile:
    """
    Extract functions, classes, interfaces, imports and references.

    Only descends into class bodies to find methods.
    Nested functions are not independently extracted in Phase 1.
    """

    if root is None:
        return ExtractedFile()

    source_bytes = source.encode("utf-8")
    result = ExtractedFile()

    function_like = ("arrow_function", "function_expression", "function")

    try:
        def walk(node: Node, inside_class: str | None, in_fn: bool = False) -> None:
            for child in node.children:

                if child.type == "function_declaration":
                    extracted = _extract_function_declaration(
                        child,
                        source_bytes,
                    )

                    if extracted:
                        result.functions.append(extracted)

                elif child.type == "lexical_declaration":
                    extracted = _extract_arrow_const(
                        child,
                        source_bytes,
                    )

                    if extracted:
                        result.functions.append(extracted)

                    else:
                        for declarator in child.children:
                            if declarator.type != "variable_declarator":
                                continue

                            value_node = declarator.child_by_field_name(
                                "value"
                            )

                            if value_node is not None and value_node.type == "object":
                                result.module_level_references.extend(
                                    _extract_module_level_references(
                                        value_node,
                                        source_bytes,
                                    )
                                )

                                object_fns = _extract_object_literal_functions(
                                    value_node,
                                    source_bytes,
                                )
                                existing = {f.name for f in result.functions}
                                for fn in object_fns:
                                    if fn.name not in existing:
                                        result.functions.append(fn)
                                        existing.add(fn.name)

                                owner_node = declarator.child_by_field_name("name")
                                if owner_node is not None and object_fns:
                                    result.module_level_objects[
                                        _node_text(owner_node, source_bytes)
                                    ] = [fn.name for fn in object_fns]

                elif child.type == "interface_declaration":
                    extracted_interface = _extract_interface(
                        child,
                        source_bytes,
                    )

                    if extracted_interface:
                        result.interfaces.append(extracted_interface)

                elif child.type == "type_alias_declaration":
                    extracted_alias = _extract_interface(
                        child,
                        source_bytes,
                    )

                    if extracted_alias:
                        result.interfaces.append(extracted_alias)

                elif child.type == "class_declaration":
                    extracted_class = _extract_class(
                        child,
                        source_bytes,
                    )

                    result.classes.append(extracted_class)

                    for grandchild in child.children:
                        if grandchild.type == "class_body":
                            for member in grandchild.children:
                                if member.type == "method_definition":
                                    method = _extract_method(
                                        member,
                                        source_bytes,
                                        extracted_class.name,
                                    )

                                    if method:
                                        result.functions.append(method)

                elif child.type == "import_statement":
                    imp = _extract_import(
                        child,
                        source_bytes,
                    )

                    if imp:
                        result.imports.append(imp)

                elif child.type == "call_expression":
                    # Module-level calls like router.post('/x', ctrl.handler).
                    # Skipped inside inline callbacks (in_fn) so calls in a
                    # handler body don't become file-level references.
                    if not in_fn:
                        _collect_call_argument_refs(child, source_bytes, result)
                    walk(child, inside_class, in_fn)

                else:
                    walk(
                        child,
                        inside_class,
                        in_fn or child.type in function_like,
                    )

        walk(root, inside_class=None)

    except Exception:
        # Preserve the existing best-effort behavior.
        return ExtractedFile()

    return result