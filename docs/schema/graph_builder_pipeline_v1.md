# Graph builder pipeline v1 — Python + TypeScript/TSX/JavaScript

Source of truth is the code in `parsing/tree_sitter/`,
`parsing/symbol_extractor/`, and `graph/builder/`. This document is
the human-readable companion — if they ever disagree, the code wins
and this file is out of date and needs updating.

## What this is

The pipeline that turns source code — now in **two language
families** — into schema-validated `GraphNode`/`GraphEdge` objects
(per `graph/schema/`, v1.0.0). Same three-layer shape as before, now
with a dispatch step added:

```
source text
    ↓  parsing/tree_sitter/{python,typescript}_parser.py   (per-language)
tree-sitter syntax tree
    ↓  parsing/symbol_extractor/{python,typescript}_symbols.py   (per-language)
ExtractedFile (functions, classes, imports — language-aware, graph-unaware)
    ↓  graph/builder/python_builder.py::build_file_graph()   (SAME for both languages)
BuildResult (GraphNode/GraphEdge list — graph-aware, language-unaware)
    ↓  graph/builder/repo_builder.py::build_repo_graph()   (orchestrates all files, all languages)
RepoBuildResult (the full repo's graph)
```

**Why `build_file_graph()` needed zero changes to support a second
language:** it was already designed to consume only the shared
`ExtractedFile` shape, never touching tree-sitter or language syntax
directly. Adding TypeScript/JSX/JavaScript meant writing a new parser
+ extractor emitting that same shape — confirmed by testing, not just
assumed by design.

**The `ExtractedFunction`/`ExtractedClass`/`ExtractedImport`/
`ExtractedFile` dataclasses moved** from living inside
`python_symbols.py` to their own `parsing/symbol_extractor/extracted_types.py`
— they were never Python-specific, and importing them from a module
named "python_symbols" for a TypeScript extractor was the wrong shape.
Confirmed via a regression test that this refactor didn't change
Python pipeline behavior at all.

## Language dispatch — `graph/builder/repo_builder.py::LANGUAGE_DISPATCH`

| Extension | Parser | Extractor | Language family |
|---|---|---|---|
| `.py` | `parse_python_source` | `python_symbols.extract_symbols` | `python` |
| `.ts` | `parse_typescript_source` | `typescript_symbols.extract_symbols` | `js` |
| `.tsx` | `parse_tsx_source` | `typescript_symbols.extract_symbols` | `js` |
| `.js` | `parse_javascript_source` | `typescript_symbols.extract_symbols` | `js` |
| `.jsx` | `parse_javascript_source` | `typescript_symbols.extract_symbols` | `js` |

**`.js`/`.jsx` share one extractor and parser** — confirmed the plain
JavaScript tree-sitter grammar natively handles JSX syntax with zero
parse errors, so a separate JSX-specific JS grammar wasn't needed
(unlike TypeScript, where `.tsx` genuinely needs the separate TSX
grammar since JSX-in-TS isn't valid plain TypeScript syntax).

## TWO import-resolution strategies — this is the real complexity here

Python and JS/TS resolve imports in fundamentally different ways, so
`language_family` determines which strategy `repo_builder.py` uses:

- **`python` family**: dotted module path (`app.services.user`),
  resolved via a repo-wide `module_index` (dotted path → file path),
  same mechanism as before TS/JS support existed.
- **`js` family**: relative file path (`./validators`,
  `../services/validators`), resolved via
  `_resolve_relative_js_import()` relative to the **importing file's
  own directory** — joins the path, collapses `..` segments, then
  tries the path as-is, with each of `.ts`/`.tsx`/`.js`/`.jsx`
  appended, then as a directory containing an `index.*` file. This
  approximates real Node.js/bundler resolution closely enough for
  Phase 1.

**Both correctly leave bare/absolute imports unresolved** rather than
guessing: Python's stdlib/third-party imports, and JS/TS's npm package
imports (`import React from "react"`) or tsconfig path aliases
(`@/components/...` — needs real config parsing, not built).

## Symbol extraction differences (TypeScript/JSX/JavaScript)

`typescript_symbols.py` handles three function forms: `function foo()`,
`const foo = () => {}` (top-level arrow function), and class methods.
**Not handled** (documented, not a bug): function expressions assigned
to object properties, anonymous default-exported arrow functions.

**Call resolution**: identical rule to Python — only plain identifier
calls (`foo()`), never member calls (`this.foo()`, `obj.foo()`).
Notably, `new UserRepository()` is **automatically** excluded here
without any extra filtering — JS/TS represents `new X()` as a distinct
`new_expression` AST node type, never even reaching the
`call_expression` matching code. (Python's equivalent — instantiating
a class via a plain call — has to be filtered by checking the resolved
target's node type instead, since Python doesn't have this AST-level
distinction.)

**Import extraction**: captures named imports (`import { a, b } from
"./x"`) and default imports (`import a from "./x"`) into
`imported_names`. Namespace imports (`import * as ns from "./x"`) are
**not** captured — every access through a namespace import is a member
call (`ns.something()`), out of scope for the same reason attribute
calls are.

## Verified

Built a mock repo matching the shape of a real Python-backend +
TypeScript-frontend monorepo (cross-directory imports included — a
`.jsx` component importing from two directories up). Confirmed in one
build: cross-file imports, cross-file calls, and cross-file
inheritance all resolved correctly across **both** language families
simultaneously, using their respective resolution strategies. Only a
genuine builtin (`len`) was left unresolved.

**Also verified against real code**, not just mocks: indexed the
actual `frontend/` directory of the project this was built for — 86
source files, producing 396 nodes (86 `File`, 309 `Function`, 1
`Class`) and 561 edges. First real confirmation the TS/JSX pipeline
holds up against a genuine, non-trivial codebase rather than only
hand-built test cases.

## Current status

- ✅ Python pipeline — unchanged in behavior, refactored to share
  `extracted_types.py`.
- ✅ TypeScript/TSX/JavaScript/JSX pipeline — built, tested against
  mocks and real code.
- ✅ Cross-language repo builds (Python + JS/TS files in the same
  repo) — verified working together in one `build_repo_graph()` call.
- ❌ npm package resolution (`node_modules` traversal) — not built,
  bare imports correctly left unresolved instead.
- ❌ tsconfig path alias resolution (`@/components/...`) — not built,
  needs real config file parsing.
- ❌ Vue, Go, Java, or any other language — not built. Adding one
  means a new parser + extractor emitting the shared `ExtractedFile`
  shape, same pattern as this addition; `graph/builder/` itself needs
  no changes.