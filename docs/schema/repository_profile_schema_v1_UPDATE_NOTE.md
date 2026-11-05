# UPDATE NOTE — entry_points detection fixed (append this section to the top of the existing repository_profile_schema_v1.md, everything else in that doc is unchanged)

`tier1_generator.py::_detect_entry_points()` was a narrow, hardcoded
candidate-path guess-list (`main.py`, `app.py`, `manage.py`,
`backend/app/main.py`, `src/main.py`). This produced a REAL bug live:
indexing this project's own `backend/` folder (where the actual entry
file is `backend/app/main.py` on disk, i.e. `app/main.py` relative to
the indexed root) matched none of the candidates, since
`"backend/app/main.py"` only matches when `repo_path` is one level
ABOVE a `backend/` folder — never when `backend/` itself IS the
indexed root. Result: `entry_points: []`, which cascaded into
dead-code analysis finding zero roots and flagging 124-of-124
candidates as dead (see `dead_code_analysis_v1.md`'s verified-live
section).

**Fixed with two additional detection passes**, both still
deterministic (no AI):

1. A wider static candidate list.
2. A content-based scan: any file named `main`/`app`/`server`/`asgi`/
   `wsgi`/`index` that actually contains a framework app-instantiation
   pattern (`FastAPI(`, `Flask(`, `express()`) is added.
3. A SECOND content-based scan, added after the first fix still
   wasn't enough: files under a conventionally-named `routes`/`route`/
   `api`/`endpoints`/`views`/`controllers` directory that contain an
   actual route-decorator pattern (`@app.get(`, `@router.post(`, etc.)
   are added — this is what catches split-out router files (e.g.
   `app/api/v1/routes/chat.py`), a completely standard FastAPI layout
   the first two passes had no way to find.

Verified live: `entry_points` went from `[]` to
`{app/main.py, app/api/v1/routes/chat.py, app/api/v1/routes/modes.py}`
against the real `backend/` repo; `completeness` correspondingly rose
from `0.727` to `0.818`.

**Still a heuristic, not a real fix to the underlying limitation**:
route decorators are still not parsed INTO THE GRAPH itself (no
`Endpoint` nodes, no `exposes` edges — those schema types remain
unpopulated, see `graph_builder_pipeline_v1.md`). This only improves
`entry_points`, one input to several features — it does not make
decorator-based routes visible to the graph's own `calls` edges or to
impact analysis.