# Repository Profile schema v1.1

Source of truth is the code in `storage/repository_profile/types.py`.
This document is the human-readable companion — if they ever disagree,
the code wins and this file is out of date and needs updating.

Schema version: `1.1.0`

## What this is

One `RepositoryProfile` per indexed repository, persisted via the
Repository Profile storage layer (storage format — JSON file, Postgres
row, Kuzu property — is an implementation detail of that layer, not
part of this contract). It's the answer to "explain authentication"
arriving as a lookup instead of a rediscovery.

## Field vs. reference rule

A field belongs on `RepositoryProfile` only if it's a **summary or
lookup value**. If the underlying thing already has its own identity
as a graph node type (`Service`, `ExternalDependency`, `Function`,
...), the Profile stores a **reference** — a graph node **id**, not a
display name — never an independently-maintained full copy. Names get
renamed; ids don't. The graph stays the single source of truth for
anything that's already a node there.

Concretely: `key_service_ids` / `important_module_ids` hold ids;
`key_dependencies` is a display-only top-N list (dependencies aren't
looked up by id elsewhere in the Profile, so a name list is fine
there); the full, authoritative dependency list is the graph's
`ExternalDependency` nodes reached via `depends_on` edges.

`entry_points` is a deliberate exception: it stays as raw paths/commands
rather than resolved node ids, because an entry point is sometimes a
command (`npm run start`, a Docker `CMD`) with no single corresponding
graph node. Paths still double as the join key to `File.path` when a
file-based entry point does resolve to one.

## Two-tier fill order

The Profile does **not** have a hidden hard dependency on the graph
builder having already run:

- **Tier 1 (repo-scan only)** — language/framework detection, coding
  style, VCS info, capabilities, entry points, build tooling,
  dependency counts. Fillable the moment the repo is cloned. Built by
  `tier1_generator.py::generate_tier1_profile()`.
- **Tier 2 (graph-dependent)** — `key_service_ids`,
  `important_module_ids`, and the graph-derived fields of
  `RepositoryStats` (`functions`, `classes`, `services`, `tests`,
  `graph_nodes`, `graph_edges`). Only fillable once the graph builder
  has run. Built by `tier2_backfill.py::backfill_tier2_profile()`.

`index_status` tracks which tier is complete. A profile is legitimately
readable while `Indexing` — callers needing only Tier 1 data don't need
to wait for `Ready`.

**Ranking note:** Tier 2's `key_service_ids`/`important_module_ids`
currently rank by plain degree count (in-edges + out-edges), computed
directly from the edge list — not a graph-library centrality algorithm.
Same signal, zero extra dependencies, easy to verify by hand. Swappable
for real centrality (NetworkX/Kuzu) later without touching anything
else that reads these fields.

**Soft-delete note:** Tier 2 backfill excludes edges/nodes marked
`is_deleted` consistently across both the ranking calculation and the
`graph_nodes`/`graph_edges` counts — a soft-deleted node's edges don't
count toward anyone's ranking, and aren't counted in the edge total
either.

## Lifecycle

```
Repository Connected  (index_status = Pending)
      ↓
Tier 1 scan runs       (index_status = Indexing)      ← generate_tier1_profile()
      ↓
Graph Built             → Tier 2 fields fill in         ← backfill_tier2_profile()
      ↓                                                    (index_status: Indexing -> Ready)
Summaries Generated     → architecture_pattern, domain_vocabulary, summary   ← NOT YET BUILT
      ↓
Embeddings Generated                                    ← NOT YET BUILT
      ↓
Ready                  (index_status = Ready, completeness ≈ 1.0)
```

A repo change after `Ready` moves `index_status` to `Stale` until
incremental reindexing completes. Any hard failure sets `Failed`.
Tier 2 backfill only ever advances `Indexing → Ready` — it never
overrides `Stale`/`Failed`; reindexing owns those transitions.

## Fields

| Field | Type | Tier | Deterministic? |
|---|---|---|---|
| `repository_id` | str | — | yes |
| `name` | str | — | yes |
| `index_status` | IndexStatus | — | yes |
| `completeness` | float (0–1) | — | yes — computed, not guessed |
| `primary_language` | str | 1 | yes |
| `languages` | dict[str, float] | 1 | yes |
| `frameworks` | list[FrameworkInfo] | 1 | yes (heuristic) |
| `architecture_pattern` (+confidence, source, confirmed_by) | str \| None | — | **Inferred** |
| `entry_points` | list[str] | 1 | yes |
| `key_service_ids` | list[str] | 2 | yes — graph `Service` node ids, ranked by degree |
| `important_module_ids` | list[str] | 2 | yes — graph `Module` node ids, ranked by degree |
| `database_systems` | list[str] | 1 | yes |
| `capabilities` | RepositoryCapabilities | 1 | yes |
| `dependency_count` | int | 1 | yes |
| `key_dependencies` | list[str] | 1 | yes |
| `domain_vocabulary` (+confidence, source, confirmed_by) | list[str] | — | **Inferred** |
| `summary` (+confidence, source, confirmed_by) | str \| None | — | **Inferred** |
| `coding_style` | CodingStyle | 1 | yes |
| `build_system` / `package_manager` / `testing_framework` | str \| None | 1 | yes — deterministic-only in v1 (see note below) |
| `stats` | RepositoryStats | mixed | yes (see sub-object) |
| `vcs` | VCSInfo | 1 | yes |
| `metadata` | ProfileMetadata | — | yes |

**On `build_system`/`package_manager`/`testing_framework` having no
provenance fields:** v1 only ever detects these deterministically, so
adding `_source`/`_confidence` to each would be unused surface area.
If AI-inference is added for one of these later, upgrade it to carry
provenance then (same pattern as `FrameworkInfo`) rather than adding
unused fields now.

Every inferred field (`architecture_pattern`, `domain_vocabulary`,
`summary`) carries a matching `_confidence` (0.0–1.0), `_source`
(`Detected`/`AI`/`User`), and **`_confirmed_by`** (user id, set only
once a developer confirms) field, plus a matching `confirm_*()` method
— same provenance discipline as the graph's `Feature` node and
`GraphEdge.confirm()`.

## Sub-objects

- **`FrameworkInfo`** — name, category, version, confidence, source.
- **`CodingStyle`** — indent style/size, quote style, line length,
  linter, formatter, and which config files each fact came from
  (`detected_from`).
- **`VCSInfo`** — branch, default_branch, last_indexed_commit,
  remote_url, is_dirty. Resolved by walking UP from the scanned path to
  find the nearest `.git` — this matters for monorepos, where
  `generate_tier1_profile()` is typically called once per subproject
  (e.g. `backend/`, `frontend/`) that each share one repo-level `.git`.
- **`RepositoryCapabilities`** — boolean flags (`has_api`,
  `has_database`, `has_tests`, `has_frontend`,
  `has_background_jobs`, `has_ci`, `has_docker`, `has_kubernetes`).
  Tier 1 — detected from config/folder presence, no graph needed.
  Answers "can I deploy this?" without a traversal.
- **`RepositoryStats`** — `files`, `lines_of_code` (Tier 1);
  `functions`, `classes`, `services`, `tests`, `graph_nodes`,
  `graph_edges` (Tier 2, require the graph).
- **`ProfileMetadata`** — `schema_version`, `generator_version`,
  `generated_at`. Factored out of the root object since more
  independently-versioned components (parser, embedding model, graph
  builder) will need their own version fields over time.

## Confidence lifecycle example

`architecture_pattern` starts as `None, confidence=0.0, source=AI,
confirmed_by=None` until an inference pass sets e.g.
`architecture_pattern="layered", confidence=0.61, source=AI`. A
developer calling `.confirm_architecture_pattern(user_id, ...)`
rewrites it to `confidence=1.0, source=User, confirmed_by=user_id` —
identical pattern to `GraphEdge.confirm()`. `domain_vocabulary` and
`summary` follow the same lifecycle via their own `confirm_*()` methods.

## Explicitly out of scope for v1

- Full dependency graphs (lives in the graph, not here).
- Per-file style overrides (only repo-wide style is captured).
- Multi-repo / monorepo umbrella profiles — one profile per indexed
  subproject for now (see `VCSInfo` note above on how monorepos are
  handled in practice).
- Resolved node ids for `entry_points` (see exception above).
- Real graph centrality for Tier 2 ranking (plain degree count for
  now — see Two-tier fill order note above).

## Current status

- ✅ **Schema** (`types.py`) — done, this document.
- ✅ **Tier 1 generator** (`tier1_generator.py`) — done. Scans a repo
  path, fills every Tier 1 field, no graph required.
- ✅ **Tier 2 backfill** (`tier2_backfill.py`) — done. Takes a Tier 1
  profile plus `GraphNode`/`GraphEdge` lists, fills `key_service_ids`,
  `important_module_ids`, graph-derived `RepositoryStats`; advances
  `index_status` to `Ready`.
- ❌ **Inferred fields generator** — NOT built. The code that actually
  calls the AI router to produce `architecture_pattern`,
  `domain_vocabulary`, and `summary` (all three currently sit at their
  defaults: `None`/`[]`, `confidence=0.0`, `source=AI` until this
  exists) is the next milestone for this module.
- ❌ **Storage layer** (`storage/repository_profile/` persistence
  itself — reading/writing a `RepositoryProfile` to disk/DB) — not
  built. Everything above only produces the object in memory.