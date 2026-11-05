"""
Symbol search — resolves a human-readable name to graph node(s).

This is the missing piece between "developer says 'the login function'"
and analyze_impact()'s requirement for an exact node_id. Node ids are
structured (`function:{path}:{name}`) and stable, but nobody types
those — this module is the lookup from plain names to those ids.

Deliberately simple for Phase 1: exact (or case-insensitive substring)
name matching over the in-memory node list. No fuzzy matching, no
embeddings-based search — that's `query/text/`, still a stubbed Phase 2
module. This covers the common case (a function/class name that
actually exists) correctly and honestly reports ambiguity rather than
guessing which of several matches was meant.
"""

from __future__ import annotations

from dataclasses import dataclass

from graph.schema.node_types import GraphNode, NodeType


class SymbolNotFoundError(Exception):
    """No node matched the given name."""


@dataclass
class AmbiguousSymbolError(Exception):
    """More than one node matched — carries the candidates so the
    caller (or the AI layer) can present them for disambiguation
    rather than guessing which one was meant."""

    name: str
    candidates: list[GraphNode]

    def __str__(self) -> str:
        options = ", ".join(f"{n.name} ({n.file_path})" for n in self.candidates)
        return f"'{self.name}' matched {len(self.candidates)} nodes: {options}"


def find_nodes_by_name(
    name: str,
    nodes: list[GraphNode],
    node_type: NodeType | None = None,
    exact: bool = True,
    include_deleted: bool = False,
) -> list[GraphNode]:
    """Returns every live node whose name matches. `exact=False` does a
    case-insensitive substring match instead — useful for "search as
    you type" in the workspace UI, but NOT what resolve_symbol() uses
    by default, since substring matching makes ambiguity far more
    likely for a single-symbol lookup.
    """
    results = []
    needle = name.lower()
    for node in nodes:
        if not include_deleted and node.is_deleted:
            continue
        if node_type is not None and node.type != node_type:
            continue
        if exact:
            if node.name == name:
                results.append(node)
        else:
            if needle in node.name.lower():
                results.append(node)
    return results


def resolve_symbol(
    name: str,
    nodes: list[GraphNode],
    node_type: NodeType | None = None,
) -> GraphNode:
    """The common-case convenience function: expect exactly one match.
    Raises SymbolNotFoundError (zero matches) or AmbiguousSymbolError
    (2+ matches) with enough context to resolve either — never silently
    picks "the first one" or "the most likely one".
    """
    matches = find_nodes_by_name(name, nodes, node_type=node_type, exact=True)
    if not matches:
        type_note = f" of type {node_type.value}" if node_type else ""
        raise SymbolNotFoundError(f"No node{type_note} found with name '{name}'")
    if len(matches) > 1:
        raise AmbiguousSymbolError(name=name, candidates=matches)
    return matches[0]