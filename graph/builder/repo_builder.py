"""
Repo-level graph builder — Python, TypeScript/TSX, and JavaScript/JSX.

Walks every supported source file in a repo, builds each one
independently via build_file_graph() (fully language-agnostic — it
only consumes the shared ExtractedFile shape), then runs a second pass
that resolves the cross-file edges the single-file builder correctly
leaves alone: imports (File->File), calls that cross a file boundary
via an actual import, inherits that cross a file boundary the same
way, and (added this session) attribute calls on an imported
module-level singleton.

TWO IMPORT-RESOLUTION STRATEGIES, because Python and JS/TS resolve
imports completely differently:
  - Python ("python" family): dotted module path (e.g. "app.services.user"),
    resolved via a repo-wide module_index built from every file's path.
  - TypeScript/JSX/JavaScript ("js" family): relative file path (e.g.
    "./validators"), resolved relative to the IMPORTING file's own
    directory — see _resolve_relative_js_import(). Bare specifiers
    (imports not starting with "." — npm packages like "react") are
    correctly left unresolved, same as Python's stdlib/third-party.

SCOPE — what's resolved and what isn't:
  - Python: absolute dotted imports only; relative imports
    (`from . import x`) not resolved in Phase 1.
  - JS/TS: relative imports only; bare npm package imports not
    resolved (no node_modules resolution); tsconfig path aliases
    (`@/components/...`) not resolved — needs real config parsing,
    a Phase 2 concern.
  - Both: a call/base name is only cross-file-resolved if it appears
    in the caller's own file's import list — mirrors real scoping in both
    languages.

CROSS-FILE SINGLETON ATTRIBUTE CALLS — added this session (pass 2d):
Fixes a real, confirmed-live gap: a module-level singleton pattern
(`cache_manager = CacheManager()` at the bottom of manager.py, then
`from ...manager import cache_manager; cache_manager.get_stats()`
from a completely different file) previously produced ZERO graph
edges — not even a CALLS edge to the method, let alone anything
pointing at the CacheManager class itself. Root cause was THREE
separate gaps, all fixed this session:
  1. Module-level instantiation assignments were never extracted at
     all — see parsing/symbol_extractor/python_symbols.py's
     module_level_instantiations, a new field.
  2. Nothing consumed BuildResult.unresolved_attribute_calls
     cross-file — passes 2a/2b/2c below only ever resolved
     imports/calls/inherits, never attribute calls. Pass 2d closes
     that gap by reusing attribute_call_resolver.py's
     resolve_local_instantiation_call() against the TARGET file's
     nodes/class-bases instead of the caller's own file's.
  3. FOUND VIA LIVE TESTING, not anticipated up front: Python
     relative imports (`from .manager import cache_manager`) were not
     resolved AT ALL — _resolve_name_via_imports only ever tried
     module_index.get(imp.module), and module_index is keyed by
     ABSOLUTE dotted paths only. A package's __init__.py commonly
     re-exports a submodule's singleton via a relative import
     (`from .manager import cache_manager` inside
     app/services/cache/__init__.py) — this is a completely standard
     pattern, not an edge case. Fixed by adding
     _resolve_python_import_target() (handles both absolute and
     relative imports, replacing the old bare module_index.get() call
     used by _resolve_name_via_imports for the python family) and
     _find_module_level_instantiation_following_reexports() (follows
     a chain of re-exporting imports — e.g. classifier.py imports
     cache_manager from the PACKAGE app.services.cache, whose
     __init__.py in turn imports it from .manager via a relative
     import — up to _MAX_REEXPORT_HOPS deep, cycle-guarded).
Scope-matched to the rest of this module: only resolved if base_name
appears in the caller's own file's import list (same "must be
genuinely imported" rule as calls/inherits), and the inheritance walk
for the final method resolution stays within the DEFINING file only — 
a singleton whose class inherits a method from a base defined in yet
another file is still left unresolved, same category of limit as
attribute_call_resolver.py's existing single-inheritance-hop,
same-file assumption.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import os
from types import SimpleNamespace

from graph.schema.edge_types import EdgeSource, EdgeType, GraphEdge
from graph.schema.graph_schema import is_valid_edge
from graph.schema.node_types import GraphNode, NodeSource, NodeType
from parsing.semantic.attribute_call_resolver import resolve_local_instantiation_call
import parsing.symbol_extractor.extracted_types as extracted_types
from parsing.symbol_extractor.python_symbols import extract_symbols as extract_python_symbols
from parsing.symbol_extractor.typescript_symbols import extract_symbols as extract_ts_symbols
from parsing.tree_sitter.python_parser import parse_python_source
from parsing.tree_sitter.typescript_parser import (
    parse_javascript_source,
    parse_tsx_source,
    parse_typescript_source,
)

# New helpers that you should add in graph/builder/parsing_utils.py
from graph.builder.parsing_utils import (
    load_tsconfig_paths_at,
    find_nearest_tsconfig_for_file,
    resolve_path_alias,
    resolve_node_module,
    create_stubs_for_bare_packages,
    record_unparsed_json,
    heuristic_extract_js_symbols,
)
from parsing.symbol_extractor.extracted_types import ExtractedFile
from .python_builder import BuildResult, _add_edge_if_valid, build_file_graph

IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".pytest_cache", ".mypy_cache", "tests",
    ".next", "coverage",
}

# extension -> (parse_fn, extract_fn, language_family)
# language_family determines which import-resolution strategy applies:
# "python" resolves via dotted module path, "js" resolves via
# relative file path (see _resolve_relative_import).
LANGUAGE_DISPATCH: dict[str, tuple] = {
    ".py": (parse_python_source, extract_python_symbols, "python"),
    ".ts": (parse_typescript_source, extract_ts_symbols, "js"),
    ".tsx": (parse_tsx_source, extract_ts_symbols, "js"),
    ".js": (parse_javascript_source, extract_ts_symbols, "js"),
    ".jsx": (parse_javascript_source, extract_ts_symbols, "js"),
}

# Tried in order when resolving a relative JS/TS import with no
# extension, e.g. "./validators" -> validators.ts, validators.tsx, ...,
# or validators/index.ts if validators is a directory.
_JS_RESOLUTION_SUFFIXES = [".ts", ".tsx", ".js", ".jsx"]


@dataclass
class FileParseResult:
    file_path: str              # relative path, forward-slash, e.g. "app/services/user.py"
    module_path: str            # dotted, e.g. "app.services.user" — Python family only, empty for js family
    language_family: str        # "python" or "js" — determines import-resolution strategy
    extracted: ExtractedFile
    build_result: BuildResult


@dataclass
class RepoBuildResult:
    nodes: list[GraphNode] = field(default_factory=list)
    edges: list[GraphEdge] = field(default_factory=list)
    files_processed: int = 0
    # Still unresolved after the cross-file pass — genuinely external
    # (stdlib, third-party, relative imports) or a resolution miss.
    unresolved_calls: list[tuple[str, str]] = field(default_factory=list)
    unresolved_bases: list[tuple[str, str]] = field(default_factory=list)
    unresolved_imports: list[tuple[str, str]] = field(default_factory=list)  # (file_path, module)
    # NEW — (caller_id, "base.method") still unresolved after pass 2d:
    # base_name wasn't imported at all, or was imported but isn't a
    # known module-level singleton in the target file (e.g. it's a
    # class, a function, or a plain value — not a `name = ClassName()` 
    # assignment), or the method genuinely isn't found even after the
    # same-file inheritance walk in the target file.
    unresolved_attribute_calls: list[tuple[str, str]] = field(default_factory=list)
    unresolved_references: list[tuple[str, str]] = field(default_factory=list)


def discover_source_files(repo_path: Path) -> list[Path]:
    files = []
    for ext in LANGUAGE_DISPATCH:
        files.extend(
            p for p in repo_path.rglob(f"*{ext}")
            if p.is_file() and not any(part in IGNORE_DIRS for part in p.parts)
        )
    return sorted(files)


def _module_path_for_file(repo_path: Path, file_path: Path) -> str:
    rel = file_path.relative_to(repo_path)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts)


def _relative_path_str(repo_path: Path, file_path: Path) -> str:
    return file_path.relative_to(repo_path).as_posix()


def _resolve_relative_js_import(
    importing_file_rel_path: str, import_module: str, known_file_paths: set[str]
) -> str | None:
    """Resolves a JS/TS relative import (e.g. './validators' from
    'app/services/userService.ts') to an actual file in the repo.

    Only handles imports starting with './' or '../' — bare specifiers
    like 'react' or 'lodash' are npm packages, correctly left
    unresolved, same as Python's stdlib/third-party imports.

    Tries, in order: the path as-is, the path + each known extension,
    and the path as a directory containing an index file — matching
    real Node.js/bundler module resolution rules closely enough for
    Phase 1 (doesn't handle package.json "main" overrides or tsconfig
    path aliases like "@/components" — those need real config parsing,
    a Phase 2 concern).
    """
    if not import_module.startswith("."):
        return None

    importing_dir = "/".join(importing_file_rel_path.split("/")[:-1])
    joined = "/".join(p for p in f"{importing_dir}/{import_module}".split("/") if p and p != ".")

    # collapse ".." segments
    parts: list[str] = []
    for part in joined.split("/"):
        if part == "..":
            if parts:
                parts.pop()
        else:
            parts.append(part)
    candidate_base = "/".join(parts)

    if candidate_base in known_file_paths:
        return candidate_base
    for suffix in _JS_RESOLUTION_SUFFIXES:
        if f"{candidate_base}{suffix}" in known_file_paths:
            return f"{candidate_base}{suffix}"
    for suffix in _JS_RESOLUTION_SUFFIXES:
        if f"{candidate_base}/index{suffix}" in known_file_paths:
            return f"{candidate_base}/index{suffix}"
    return None


def build_repo_graph(repo_path: str | Path) -> RepoBuildResult:
    repo_path = Path(repo_path).resolve()
    if not repo_path.is_dir():
        raise ValueError(f"repo_path does not exist or is not a directory: {repo_path}")

    # Load tsconfig/jsconfig path mappings once for the repo (used for "@/..." aliases)
    # Load tsconfig/jsconfig path mappings once for the repo.
# Use the nearest tsconfig found from the repository root.
    tsconfig_path = find_nearest_tsconfig_for_file(
        str(repo_path),
        "",
    )

    if tsconfig_path:
        base_url, ts_paths = load_tsconfig_paths_at(tsconfig_path)
    else:
        base_url, ts_paths = None, {}

    # --- pass 1: build every file independently -----------------------
    file_results: list[FileParseResult] = []
    for file_path in discover_source_files(repo_path):
        rel_path = _relative_path_str(repo_path, file_path)
        parse_fn, extract_fn, language_family = LANGUAGE_DISPATCH[file_path.suffix]
        try:
            source = file_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        # Try to parse + extract; if parser/extractor fails, record the
        # unparsed file and continue with a minimal extracted object so
        # the rest of the pipeline can run (we also persist the raw
        # content for later AI/engineer-mode processing).
        try:
            root = parse_fn(source)
            extracted = extract_fn(root, source)
        except Exception as e:
            # Record the failure so it can be handled by the AI/engineer
            # workflow later. If you have DB wiring, replace record_unparsed_json
            # with a DB insert.
            try:
                record_unparsed_json(repository_id=repo_path.name, file_path=rel_path, raw_content=source, reason=str(e))
            except Exception:
                # Never crash the whole build if recording fails
                pass
            # Use a real ExtractedFile() as the minimal object to keep
            # types consistent for the rest of the pipeline.
            # Use ExtractedFile() as the minimal object to keep types consistent.
            extracted = extracted_types.ExtractedFile()
            # Best-effort heuristic for JS/TS files: find imports & functions via regex
            if file_path.suffix in (".ts", ".tsx", ".js", ".jsx"):
                heur = heuristic_extract_js_symbols(source)
                # build simple import objects compatible with the extractor shape:
                for m in heur.get("imports", []):
                    # The ExtractedImport dataclass constructor signature used elsewhere:
                    try:
                        from parsing.symbol_extractor.extracted_types import ExtractedImport
                        extracted.imports.append(ExtractedImport(module=m, imported_names=[], line=0))
                    except Exception:
                        pass

        build_result = build_file_graph(rel_path, extracted)
        module_path = _module_path_for_file(repo_path, file_path) if language_family == "python" else ""
        file_results.append(FileParseResult(
            file_path=rel_path,
            module_path=module_path,
            language_family=language_family,
            extracted=extracted,
            build_result=build_result,
        ))

        # --- create small stubs for bare npm packages so imports like 'react'
    # resolve inside this repo (keeps everything self-contained).
    pkg_stub_map = create_stubs_for_bare_packages(str(repo_path), file_results)

        # --- normalize stub mapping and create asset stubs -----------------
    normalized_map: dict[str, str] = {}
    for k, v in pkg_stub_map.items():
        normalized_map[k] = v
        if k.startswith("@"):
            parts = k.split("/")
            pkg_base = "/".join(parts[:2]) if len(parts) >= 2 else k
        else:
            pkg_base = k.split("/")[0]
        if pkg_base not in normalized_map:
            normalized_map[pkg_base] = v
    pkg_stub_map = normalized_map

    # Create simple asset stubs for relative non-code imports (e.g. ./globals.css).
    asset_stub_prefix = ".stubs/assets"
    asset_stub_relpaths: set[str] = set()
    asset_stub_map: dict[tuple[str, str], str] = {}
    for fr in list(file_results):  # iterate a snapshot
        for imp in getattr(fr.extracted, "imports", []):
            mod = getattr(imp, "module", "")
            if not mod:
                continue
            if mod.startswith(".") and any(mod.endswith(ext) for ext in (".css", ".scss", ".sass", ".png", ".jpg", ".jpeg", ".svg")):
                # Compute absolute importer directory based on the repo root + file's rel path
                abs_importer = (Path(repo_path) / fr.file_path).resolve()
                importer_dir = abs_importer.parent
                # Candidate absolute path for the imported asset
                candidate_abs = (importer_dir / mod).resolve()
                # Try to make the stub repo-relative; if candidate_abs isn't under repo_root, fall back to a safe path
                try:
                    rel = candidate_abs.relative_to(Path(repo_path).resolve()).as_posix()
                    stub_rel = f"{asset_stub_prefix}/{rel}"
                except Exception:
                    # Fallback: create a normalized path using the importing file's directory + the import path
                    safe_rel = os.path.normpath(str(Path(fr.file_path).parent / mod)).replace("\\", "/").lstrip("./")
                    stub_rel = f"{asset_stub_prefix}/{safe_rel}"

                stub_abs = Path(repo_path) / stub_rel
                stub_abs.parent.mkdir(parents=True, exist_ok=True)
                if not stub_abs.exists():
                    stub_abs.write_text("/* asset stub */", encoding="utf-8")
                asset_stub_relpaths.add(stub_rel)
                asset_stub_map[(fr.file_path, mod)] = stub_rel

                # Add a minimal FileParseResult for the stub if not present
                if not any(fr2.file_path == stub_rel for fr2 in file_results):
                    fake_extracted = extracted_types.ExtractedFile()
                    fake_build = build_file_graph(stub_rel, fake_extracted)
                    file_results.append(FileParseResult(
                        file_path=stub_rel,
                        module_path="",
                        language_family="js",
                        extracted=fake_extracted,
                        build_result=fake_build,
                    ))

    # Parse the created stubs and add them to file_results so they are
    # included in known_file_paths and node lookups.
    for pkg_name, stub_rel in pkg_stub_map.items():
        stub_abs = (Path(repo_path) / stub_rel).resolve()
        if not stub_abs.exists():
            continue
        # Choose parser by extension
        parse_fn, extract_fn, language_family = LANGUAGE_DISPATCH.get(stub_abs.suffix, (None, None, None))
        if parse_fn is None:
            # default to TypeScript (.ts)
            parse_fn, extract_fn, language_family = LANGUAGE_DISPATCH[".ts"]
        try:
            source = stub_abs.read_text(encoding="utf-8", errors="ignore")
            root = parse_fn(source)
            extracted = extract_fn(root, source) if root is not None else extracted_types.ExtractedFile()
        except Exception:
            source = ""
            root = None
            extracted = extracted_types.ExtractedFile()
        build_result = build_file_graph(stub_rel, extracted)
        file_results.append(FileParseResult(
            file_path=stub_rel,
            module_path="",
            language_family=language_family,
            extracted=extracted,
            build_result=build_result,
        ))
        
    module_index: dict[str, str] = {
        fr.module_path: fr.file_path for fr in file_results if fr.language_family == "python"
    }
    known_file_paths: set[str] = {fr.file_path for fr in file_results}
    # Lookups into each file's already-built nodes, keyed by (file_path, node_type, name)
    nodes_by_file: dict[str, list[GraphNode]] = {
        fr.file_path: fr.build_result.nodes for fr in file_results
    }
    file_node_by_path: dict[str, GraphNode] = {
        fr.file_path: next(n for n in fr.build_result.nodes if n.type == NodeType.FILE)
        for fr in file_results
    }
    # NEW — per-file lookup tables feeding pass 2d below.
    module_level_instantiations_by_file: dict[str, dict[str, str]] = {
        fr.file_path: fr.build_result.module_level_instantiations for fr in file_results
    }
    file_results_by_path: dict[str, FileParseResult] = {fr.file_path: fr for fr in file_results}
    class_bases_by_file: dict[str, dict[str, list[str]]] = {
        fr.file_path: {ec.name: ec.base_class_names for ec in fr.extracted.classes}
        for fr in file_results
    }

    repo_result = RepoBuildResult()
    for fr in file_results:
        repo_result.nodes.extend(fr.build_result.nodes)
        repo_result.edges.extend(fr.build_result.edges)
    repo_result.files_processed = len(file_results)

    # --- pass 2a: resolve imports (File -> File) -----------------------
    for fr in file_results:
        this_file_node = file_node_by_path[fr.file_path]
        for imp in fr.extracted.imports:
            if fr.language_family == "python":
                target_file_path = module_index.get(imp.module)
            else:
                # 1) try relative resolution
                target_file_path = _resolve_relative_js_import(fr.file_path, imp.module, known_file_paths)
                # 2) if not relative, try nearest tsconfig paths (monorepo-aware)
                if target_file_path is None:
                    tsconfig_path = find_nearest_tsconfig_for_file(str(repo_path), fr.file_path)
                    if tsconfig_path:
                        base_url, ts_paths_local = load_tsconfig_paths_at(tsconfig_path)
                        if ts_paths_local:
                            resolved_fs = resolve_path_alias(imp.module, str(repo_path), base_url, ts_paths_local)
                            if resolved_fs:
                                try:
                                    rel = Path(resolved_fs).resolve().relative_to(repo_path).as_posix()
                                    target_file_path = rel
                                except Exception:
                                    target_file_path = os.path.normpath(resolved_fs)
                # 3) try node_modules resolution (repo-local) — only
                # accept it if that file was actually parsed (node_modules
                # is excluded from discovery via IGNORE_DIRS, so a hit
                # here is usually unusable and should fall through to stubs)
                if target_file_path is None:
                    nm = resolve_node_module(imp.module, str(repo_path))
                    if nm and nm.replace("\\", "/") in known_file_paths:
                        target_file_path = nm
               # 4) if still unresolved, try stubs with tolerant matching
                if target_file_path is None:
    # direct stub for exact specifier
                    if imp.module in pkg_stub_map:
                        target_file_path = pkg_stub_map[imp.module]
                    else:
        # try scoped package base (e.g. "@org/pkg/..." -> "@org/pkg")
                        if imp.module.startswith("@"):
                            parts = imp.module.split("/")
                            pkg_base = "/".join(parts[:2]) if len(parts) >= 2 else imp.module
                        else:
                            pkg_base = imp.module.split("/")[0]
                        if pkg_base in pkg_stub_map:
                            target_file_path = pkg_stub_map[pkg_base]
                # 5) relative asset imports (css/scss/images) resolved via asset_stub_map
                if target_file_path is None:
                    target_file_path = asset_stub_map.get((fr.file_path, imp.module))            

# normalize Windows backslashes to forward slashes (file_node_by_path uses posix paths)
                if target_file_path is not None:
                    target_file_path = target_file_path.replace("\\", "/")
                    if target_file_path.startswith("./"):
                        target_file_path = target_file_path[2:]

            if target_file_path is not None and target_file_path in file_node_by_path:
                target_file_node = file_node_by_path[target_file_path]
                _add_edge_if_valid(
                    _AsBuildResult(repo_result), this_file_node, target_file_node, EdgeType.IMPORTS
                )
            else:
                # Stdlib/npm package, or a genuinely unresolvable import —
                # not resolvable against this repo's own files.
                repo_result.unresolved_imports.append((fr.file_path, imp.module))

    # --- pass 2b: resolve cross-file calls -----------------------------
    for fr in file_results:
        for caller_id, called_name in fr.build_result.unresolved_calls:
            target_file_path = _resolve_name_via_imports(
                called_name, fr.extracted, fr.file_path, fr.language_family, module_index, known_file_paths,
                repo_root=repo_path, base_url=base_url, ts_paths=ts_paths, pkg_stub_map=pkg_stub_map,
            )
            resolved = False
            if target_file_path is not None:
                target_function = _find_top_level_function(
                    nodes_by_file[target_file_path], called_name
                )
                if target_function is not None:
                    caller_node = _find_node_by_id(fr.build_result.nodes, caller_id)
                    if caller_node is not None:
                        resolved = _add_edge_if_valid(
                            _AsBuildResult(repo_result), caller_node, target_function, EdgeType.CALLS
                        )
            if not resolved:
                repo_result.unresolved_calls.append((caller_id, called_name))

    # --- pass 2c: resolve cross-file inherits ---------------------------
    for fr in file_results:
        for child_id, base_name in fr.build_result.unresolved_bases:
            target_file_path = _resolve_name_via_imports(
                base_name, fr.extracted, fr.file_path, fr.language_family, module_index, known_file_paths,
                repo_root=repo_path, base_url=base_url, ts_paths=ts_paths, pkg_stub_map=pkg_stub_map,
            )
            resolved = False
            if target_file_path is not None:
                target_class = _find_class(nodes_by_file[target_file_path], base_name)
                if target_class is not None:
                    child_node = _find_node_by_id(fr.build_result.nodes, child_id)
                    if child_node is not None:
                        resolved = _add_edge_if_valid(
                            _AsBuildResult(repo_result), child_node, target_class, EdgeType.INHERITS
                        )
            if not resolved:
                repo_result.unresolved_bases.append((child_id, base_name))

    # --- pass 2d: resolve cross-file singleton attribute calls ----------
    # (caller.base_name.method_name() where base_name is an imported
    # module-level singleton, e.g. `cache_manager.get_stats()`)
    for fr in file_results:
        for caller_id, combined in fr.build_result.unresolved_attribute_calls:
            base_name, _sep, method_name = combined.partition(".")
            target_file_path = _resolve_name_via_imports(
                base_name, fr.extracted, fr.file_path, fr.language_family, module_index, known_file_paths,
                repo_root=repo_path, base_url=base_url, ts_paths=ts_paths, pkg_stub_map=pkg_stub_map,
            )

            resolved = False
            if target_file_path is not None and fr.language_family == "python":
                # Follows re-export chains (e.g. a package __init__.py
                # re-exporting a submodule's singleton via a relative
                # import) rather than only checking target_file_path
                # directly — see module docstring, gap #3.
                found = _find_module_level_instantiation_following_reexports(
                    base_name, target_file_path, module_level_instantiations_by_file,
                    file_results_by_path, module_index, known_file_paths,
                )

                if found is not None:
                    defining_file_path, instantiated_class_name = found
                    caller_node = _find_node_by_id(fr.build_result.nodes, caller_id)
                    target_nodes = nodes_by_file[defining_file_path]

                    if caller_node is not None:
                        # REFERENCES: caller -> the instantiated class itself.
                        # This is the edge that actually closes the impact-
                        # analysis gap — present even if the specific method
                        # below can't be resolved (e.g. inherited from a
                        # third file), because "this code uses this class"
                        # is true regardless of which method was called.
                        target_class_node = _find_class(target_nodes, instantiated_class_name)
                        if target_class_node is not None:
                            ref_added = _add_edge_if_valid(
                                _AsBuildResult(repo_result), caller_node, target_class_node, EdgeType.REFERENCES
                            )
                            resolved = resolved or ref_added

                        # CALLS: caller -> the specific method, resolved via
                        # the SAME inheritance-walking logic already used for
                        # same-file resolution — just pointed at the file
                        # that actually DEFINES the singleton (which may be
                        # different from target_file_path if a re-export
                        # chain was followed) instead of the caller's own.
                        target_method = resolve_local_instantiation_call(
                            instantiated_class_name, method_name, target_nodes,
                            class_bases_by_file.get(defining_file_path, {}), defining_file_path,
                        )
                        if target_method is not None:
                            call_added = _add_edge_if_valid(
                                _AsBuildResult(repo_result), caller_node, target_method, EdgeType.CALLS
                            )
                            resolved = resolved or call_added

            elif target_file_path is not None:
                target_fr = file_results_by_path.get(target_file_path)
                owned = (
                    getattr(target_fr.extracted, "module_level_objects", {}).get(base_name)
                    if target_fr is not None
                    else None
                )
                if owned and method_name in owned:
                    caller_node = _find_node_by_id(fr.build_result.nodes, caller_id)
                    target_method = _find_top_level_function(
                        nodes_by_file[target_file_path], method_name
                    )
                    if caller_node is not None and target_method is not None:
                        edge_type = (
                            EdgeType.REFERENCES
                            if caller_node.type.value == "File"
                            else EdgeType.CALLS
                        )
                        resolved = _add_edge_if_valid(
                            _AsBuildResult(repo_result), caller_node, target_method, edge_type
                        )

            if not resolved:
                repo_result.unresolved_attribute_calls.append((caller_id, combined))

    # --- pass 2e: resolve cross-file references -------------------------
    for fr in file_results:
        for from_id, name in fr.build_result.unresolved_references:
            target_file_path = _resolve_name_via_imports(
                name, fr.extracted, fr.file_path, fr.language_family, module_index, known_file_paths,
                repo_root=repo_path, base_url=base_url, ts_paths=ts_paths, pkg_stub_map=pkg_stub_map,
            )
            resolved = False
            if target_file_path is not None:
                # Checks classes too, not just top-level functions — a
                # cross-file reference (e.g. a type annotation like
                # `req: GraphNodeResponse` importing from another file)
                # points at a class far more often than a function. See
                # python_builder.py's same-file references pass for the
                # matching fix and why this limitation existed.
                target_nodes = nodes_by_file[target_file_path]

                target = (
                    _find_top_level_function(target_nodes, name)
                    or _find_class(target_nodes, name)
                    or _find_interface(target_nodes, name)
                )
                if target is not None:
                    from_node = _find_node_by_id(fr.build_result.nodes, from_id)
                    if from_node is not None:
                        resolved = _add_edge_if_valid(
                            _AsBuildResult(repo_result), from_node, target, EdgeType.REFERENCES
                        )
            if not resolved:
                repo_result.unresolved_references.append((from_id, name))

    return repo_result


class _AsBuildResult:
    """Lets _add_edge_if_valid() (which appends to `.edges`) work
    against a RepoBuildResult without duplicating that function."""
    def __init__(self, repo_result: RepoBuildResult) -> None:
        self.edges = repo_result.edges


def _resolve_python_import_target(
    importing_file_path: str, import_module: str, module_index: dict[str, str]
) -> str | None:
    """Resolves a Python import (module string as captured by the
    extractor) to a file path — handles BOTH absolute dotted imports
    ('app.services.cache') and relative imports ('.manager',
    '..sibling.thing'). Previously only the absolute case was handled
    at all (a bare module_index.get(imp.module) call) — relative
    imports, a completely standard Python pattern (especially common
    in package __init__.py re-exports), silently never resolved.

    Relative-import resolution mirrors real Python import semantics:
    one leading dot = the CURRENT package (the package containing
    importing_file_path); each additional dot walks up one more
    parent package. A file named __init__.py IS itself the package
    root, so its "current package" is its own directory, same as any
    other module in that directory.
    """
    if not import_module.startswith("."):
        return module_index.get(import_module)

    dot_count = len(import_module) - len(import_module.lstrip("."))
    remainder = import_module[dot_count:]  # e.g. "manager", or "" for `from . import x`

    if "/" in importing_file_path:
        dir_part = importing_file_path.rsplit("/", 1)[0]
    else:
        dir_part = ""
    package_parts = dir_part.split("/") if dir_part else []

    levels_up = dot_count - 1  # one dot = stay in current package, no walk-up
    if levels_up > 0:
        if levels_up > len(package_parts):
            return None  # would walk above repo root — malformed/unresolvable
        package_parts = package_parts[: len(package_parts) - levels_up]

    target_parts = package_parts + remainder.split(".") if remainder else package_parts
    if not target_parts:
        return None
    return module_index.get(".".join(target_parts))


_MAX_REEXPORT_HOPS = 5


def _find_module_level_instantiation_following_reexports(
    name: str,
    file_path: str,
    module_level_instantiations_by_file: dict[str, dict[str, str]],
    file_results_by_path: dict[str, "FileParseResult"],
    module_index: dict[str, str],
    known_file_paths: set[str],
    _hop: int = 0,
) -> tuple[str, str] | None:
    """Returns (defining_file_path, ClassName) for `name`, following a
    chain of re-exporting imports if `file_path` doesn't itself
    instantiate `name` but instead imports it from somewhere else
    (the standard package `__init__.py` re-export pattern — e.g.
    `from .manager import cache_manager` inside
    app/services/cache/__init__.py). Cycle/depth-guarded at
    _MAX_REEXPORT_HOPS — a real re-export chain this deep would be
    unusual; this exists to guarantee termination, not because 5 hops
    is a meaningful design limit.
    """
    if _hop >= _MAX_REEXPORT_HOPS:
        return None

    direct = module_level_instantiations_by_file.get(file_path, {}).get(name)
    if direct is not None:
        return file_path, direct

    fr = file_results_by_path.get(file_path)
    if fr is None:
        return None

    for imp in fr.extracted.imports:
        if name not in imp.imported_names:
            continue
        if fr.language_family == "python":
            next_file = _resolve_python_import_target(file_path, imp.module, module_index)
        else:
            next_file = _resolve_relative_js_import(file_path, imp.module, known_file_paths)

        if next_file is not None and next_file != file_path:
            result = _find_module_level_instantiation_following_reexports(
                name, next_file, module_level_instantiations_by_file, file_results_by_path,
                module_index, known_file_paths, _hop + 1,
            )
            if result is not None:
                return result

    return None


def _resolve_name_via_imports(
    name: str,
    extracted: ExtractedFile,
    importing_file_path: str,
    language_family: str,
    module_index: dict[str, str],
    known_file_paths: set[str],
    repo_root: Path | str | None = None,
    base_url: str | None = None,
    ts_paths: dict | None = None,
    pkg_stub_map: dict[str, str] | None = None,
) -> str | None:
    """
    Given a symbol name and the calling file's ExtractedFile, find the
    file that provided that name by iterating the importing file's
    imports; supports Python resolution and JS/TS relative imports,
    and if ts_paths is provided will also attempt tsconfig/jsconfig
    path alias resolution for JS/TS.
    """
    for imp in extracted.imports:
        if name in imp.imported_names:
            if language_family == "python":
                return _resolve_python_import_target(importing_file_path, imp.module, module_index)
            # JS family: prefer relative resolution, but fall back to tsconfig alias resolver
            target = _resolve_relative_js_import(importing_file_path, imp.module, known_file_paths)
            if target is not None:
                return target
            if ts_paths:
                resolved_fs = resolve_path_alias(imp.module, str(repo_root or ""), base_url, ts_paths)
                if resolved_fs:
                    try:
                        rel = Path(resolved_fs).resolve().relative_to(Path(repo_root).resolve()).as_posix()
                        return rel
                    except Exception:
                        return os.path.normpath(resolved_fs)
            if pkg_stub_map and imp.module in pkg_stub_map:
                return pkg_stub_map[imp.module]        
    return None


def _find_top_level_function(nodes: list[GraphNode], name: str) -> GraphNode | None:
    for n in nodes:
        if n.type == NodeType.FUNCTION and n.name == name and "." not in n.id.rsplit(":", 1)[-1]:
            return n
    return None


def _find_class(nodes: list[GraphNode], name: str) -> GraphNode | None:
    for n in nodes:
        if n.type == NodeType.CLASS and n.name == name:
            return n
    return None

def _find_interface(nodes: list[GraphNode], name: str) -> GraphNode | None:
    for n in nodes:
        if n.type == NodeType.INTERFACE and n.name == name:
            return n
    return None

def _find_node_by_id(nodes: list[GraphNode], node_id: str) -> GraphNode | None:
    for n in nodes:
        if n.id == node_id:
            return n
    return None