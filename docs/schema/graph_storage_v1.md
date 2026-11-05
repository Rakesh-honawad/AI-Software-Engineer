# Graph storage v1 — JSON backend

Source of truth is the code in `storage/graph/json_store.py`.

## What this is

Persists a built graph (list of `GraphNode` + list of `GraphEdge`) to a
single JSON file, and reads it back exactly. This is what lets the
graph survive past one process run — without it, `graph/builder/`
only ever produces an in-memory result that vanishes when the script ends.

## Why JSON, not Kuzu (the blueprint's actual target)

Same pattern used everywhere else in this build (degree-based ranking
in Tier 2, dependency-injected model calls in narration): ship the
simplest correct version first, keep the function signatures
(`save_graph` / `load_graph` / `compute_graph_hash`) as the permanent
contract, swap the implementation for a real graph DB later without
touching any caller.

## API

- `save_graph(path, nodes, edges, repository_id, repository_commit, graph_id="") -> GraphSnapshotMetadata`
- `load_graph(path, verify_integrity=True) -> (nodes, edges, metadata)`
- `compute_graph_hash(nodes, edges) -> str`

## Two safety checks, both load-bearing

- **`GraphIntegrityError`** — every save computes a sha256 hash over
  the full, sorted node/edge content; every load recomputes it and
  compares against the stored value. Catches a hand-edited, truncated,
  or otherwise corrupted file immediately, rather than silently
  returning bad data. Can be bypassed explicitly with
  `verify_integrity=False` (e.g. for inspecting a flagged file), but
  never silently skipped.
- **`SchemaVersionMismatch`** — compares the file's stored
  `schema_version` against the currently-running `SCHEMA_VERSION`
  (from `graph/schema/schema_version.py`) and raises rather than
  guessing how to reinterpret an old shape. **No auto-migration** —
  that's a decision to make explicitly later (a real migration
  function per version bump), not something to paper over.

## Hash design

`compute_graph_hash` is content-based and **order-independent** — it
serializes every node/edge to a canonical JSON string, sorts those
strings, then hashes the joined result. Two graphs with identical
content but differently-ordered lists produce the identical hash. This
is what lets a caller cheaply check "has this repo's graph actually
changed" without a full structural diff.

## Verified (round-trip tests run against the real multi-file mock repo)

- Save → load round-trip: node/edge counts and every node id match
  exactly.
- Hash stability: shuffling the node list before hashing produced the
  identical hash.
- Tampering: hand-editing a saved file's node name correctly raised
  `GraphIntegrityError` on load.
- Version drift: hand-editing a saved file's `schema_version` correctly
  raised `SchemaVersionMismatch` on load.
- `verify_integrity=False` correctly bypasses the check when needed.

## Current status

- ✅ Save/load/hash — built and tested.
- ❌ Kuzu (or any real graph DB) backend — not built. This module's
  contract is the target for that swap.
- ❌ No incremental save (partial update of just the changed portion of
  a graph) — every save currently rewrites the whole file. Fine at
  MVP scale; revisit once a repo's graph is large enough for this to
  matter.
- ❌ Not yet wired into `graph/builder/repo_builder.py` — building and
  persisting are still two manual steps a caller does separately, not
  one pipeline call.