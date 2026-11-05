# Query layer v1

Source of truth is the code in `query/symbol/symbol_search.py` and
`query/graph/graph_query.py`.

## What this is

The missing link between "a developer types a name" and
`analyze_impact()`'s hard requirement for an exact `node_id`. Node ids
are stable and structured (`function:{path}:{name}`) but nobody types
those — this layer resolves plain names to them, and provides the
basic node/neighbor lookups that would otherwise get reimplemented
separately by impact analysis, the API, and the workspace UI.

## `query/symbol/symbol_search.py`

`resolve_symbol(name, nodes, node_type=None) -> GraphNode` — the
primary entry point. Never guesses:

- Zero matches → raises `SymbolNotFoundError`
- Exactly one match → returns it
- 2+ matches → raises `AmbiguousSymbolError`, which carries every
  candidate (with `file_path`) so the caller can present them for
  disambiguation rather than silently picking one. The API's impact
  route translates this into a 409 with the candidate list in the
  response body — see `api_v1.md`.

`find_nodes_by_name(name, nodes, node_type=None, exact=True)` — the
lower-level function `resolve_symbol` is built on. `exact=False` does
a case-insensitive substring match instead, intended for "search as
you type" in the workspace UI, not for a single-symbol lookup (where
substring matching makes ambiguity far more likely).

**Deliberately no fuzzy/embeddings-based matching** — that's
`query/text/`, still a stubbed Phase 2 module. This covers the common
case (the name actually exists) correctly and honestly, rather than
guessing at typos.

**Implementation note:** `AmbiguousSymbolError` is both a `@dataclass`
and an `Exception` subclass — verified this combination actually works
correctly (raises, catches as the specific type, fields accessible)
before relying on it, since dataclass + Exception can behave
unexpectedly if not tested.

## `query/graph/graph_query.py`

Three general-purpose lookups, not impact-analysis-specific (that
stays in `analysis/impact/`):

- `get_node(node_id, nodes) -> GraphNode | None`
- `find_nodes_by_type(node_type, nodes, include_deleted=False) -> list[GraphNode]`
- `get_neighbors(node_id, edges, direction="in"|"out"|"both", edge_types=None) -> list[tuple[GraphEdge, str]]`

All three exclude soft-deleted nodes by default, consistent with every
other module that touches the graph.

## Current status

- ✅ Both modules built and tested against the real multi-file mock
  graph: exact match, type-filtered match, not-found, a manufactured
  ambiguous case (two `save` functions in different files), substring
  search, and bidirectional neighbor traversal with edge-type
  filtering.
- ❌ `query/text/` (embeddings/fuzzy search) — still Phase 2, stubbed.