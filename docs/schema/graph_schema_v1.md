# Graph builder pipeline v1 — Python

Source of truth is the code in `parsing/tree_sitter/python_parser.py`,
`parsing/symbol_extractor/python_symbols.py`, and
`graph/builder/python_builder.py`. This document is the human-readable
companion — if they ever disagree, the code wins and this file is out
of date and needs updating.

## What this is

The pipeline that turns Python source code into schema-validated
`GraphNode`/`GraphEdge` objects (per `graph/schema/`, v1.0.0). Three
layers, each with exactly one job:

```
source text
    ↓  parsing/tree_sitter/python_parser.py
tree-sitter syntax tree
    ↓  parsing/symbol_extractor/python_symbols.py
ExtractedFile (functions, classes, imports — language-aware, graph-unaware)
    ↓  graph/builder/python_builder.py
BuildResult (GraphNode/GraphEdge list — graph-aware, language-unaware)
```

**Why three layers, not one file:** adding a second language (e.g.
TypeScript) later means writing a new parser + extractor that emit the
same `ExtractedFile` shape — the builder doesn't change at all, since
it never sees tree-sitter or Python syntax, only the normalized
dataclasses.

## Scope: single file, same-file resolution only

This is the current, load-bearing limitation — not an oversight:

- **`calls` edges** only resolve a plain call like `validate(x)` to a
  `Function` node defined in the **same file**. Attribute calls like
  `self.foo()` or `obj.method()` are not resolved — that requires
  knowing what `self`/`obj` actually *is* (type resolution), which is
  `parsing/semantic/`, still a Phase 2 stub. Skipping these rather than
  guessing keeps every `calls` edge this pipeline produces fully
  deterministic — no heuristic edge pretends to be AST-certain.
- **`inherits` edges** only resolve base classes defined in the same
  file. A class inheriting from an imported base is recorded in
  `BuildResult.unresolved_bases` rather than silently dropped or
  guessed at.
- **`imports` edges are not emitted yet.** The schema's `imports` edge
  is `File → File`, but resolving an import statement to an actual file
  requires knowing the whole repo's module-path → file-path layout —
  a repo-level concern, not a single-file one.
- **Class instantiations are correctly never mistaken for calls.** A
  call like `UserRepository()` cannot resolve to a `calls` edge: the
  call-resolution lookup used by the builder only ever contains
  `Function` nodes (built separately from the `Class` lookup), so a
  class can never be accidentally selected as a call target — and
  `is_valid_edge()` from the graph schema is the second line of
  defense if that ever changed.

**What's still needed on top of this (not yet built):** a repo-level
orchestrator that walks every file, calls `build_file_graph()` on each,
then runs a second resolution pass across all files' fragments together
to fill in the cross-file `calls`, `inherits`, and `imports` edges this
version correctly leaves alone.

## Layer 1 — `parsing/tree_sitter/python_parser.py`

One function: `parse_python_source(source: str) -> Node`. Thin by
design — its only job is turning text into a tree-sitter syntax tree.
No interpretation of what the tree means happens here.

## Layer 2 — `parsing/symbol_extractor/python_symbols.py`

Walks the syntax tree, produces four dataclasses:

| Dataclass | Captures |
|---|---|
| `ExtractedFunction` | name, 1-indexed line range, `parent_class` (`None` for top-level), `called_names` (unresolved call targets found in the body) |
| `ExtractedClass` | name, line range, `base_class_names` |
| `ExtractedImport` | module path, imported names (empty for plain `import x`), line number |
| `ExtractedFile` | lists of the three above for one file |

**Nesting depth:** only descends one level into class bodies to find
methods. Deeply nested functions (closures) are out of scope for Phase
1 — they're implementation detail of their enclosing function, not
independently graph-worthy nodes (see the node-vs-property rule in
`graph/schema/node_types.py`).

**Call extraction:** only captures calls where the called expression is
a plain identifier (`foo(...)`). Attribute-style calls
(`func_node.type == "attribute"`) are deliberately skipped at this
layer already — see Scope section above.

Entry point: `extract_symbols(root: Node, source: str) -> ExtractedFile`.

## Layer 3 — `graph/builder/python_builder.py`

Entry point: `build_file_graph(file_path: str, extracted: ExtractedFile) -> BuildResult`.

`BuildResult` carries:
- `nodes: list[GraphNode]`
- `edges: list[GraphEdge]`
- `unresolved_calls: list[str]` — call targets that couldn't (or
  deliberately wouldn't) resolve: builtins, imported functions, class
  instantiations, or self-recursion. Recorded for visibility, not
  silently dropped.
- `unresolved_bases: list[str]` — base classes not defined in this file
  (almost certainly imported).

**Node id convention** (stable across incremental reindexing runs, so
the same function always gets the same id):
```
file:{file_path}
class:{file_path}:{ClassName}
function:{file_path}:{function_name}                    (top-level)
function:{file_path}:{ClassName}.{method_name}           (method)
```

**Every edge is validated before being added** — `_add_edge_if_valid()`
calls `is_valid_edge()` from `graph/schema/graph_schema.py` and silently
skips (rather than raises on, or force-adds) any edge whose endpoint
types don't match the schema's `VALID_EDGE_ENDPOINTS` table. This is
the safety net that catches a resolution mistake before it corrupts the
graph.

**All nodes/edges from this pipeline use `source=AST`, `confidence=1.0`**
— nothing here is inferred, everything is a direct, deterministic
reading of the parsed syntax tree.

## Current status

- ✅ Parser, extractor, builder — all three built and tested against
  hand-written multi-class source and against a real (minimal)
  `main.py`.
- ✅ Containment, inheritance, and same-file call edges verified
  correct, including the class-instantiation false-positive case.
- ❌ Repo-level orchestrator (walk all files, resolve cross-file edges)
  — not built. This is the next milestone for the builder.
- ❌ TypeScript (or any second language) parser/extractor — not built.
  The builder is already language-agnostic and ready for one once an
  extractor emits the same `ExtractedFile` shape.