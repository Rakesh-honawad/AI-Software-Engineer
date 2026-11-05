import json
import os
import re
from glob import glob
from typing import Dict, Optional, Tuple, List, Iterable, Set
from pathlib import Path

# --- tsconfig / path alias helpers -----------------------------------------

def load_tsconfig_paths_at(tsconfig_path: str) -> Tuple[Optional[str], Dict[str, List[str]]]:
    """Load (baseUrl, paths) from an explicit tsconfig/json path."""
    if not tsconfig_path or not os.path.exists(tsconfig_path):
        return None, {}
    try:
        conf = json.load(open(tsconfig_path, "r", encoding="utf-8"))
        opts = conf.get("compilerOptions", {})
        base = opts.get("baseUrl", "")
        paths = opts.get("paths", {})
        return base, paths
    except Exception:
        return None, {}

def find_nearest_tsconfig_for_file(repo_root: str, file_rel_path: str) -> Optional[str]:
    """
    Walk from the file's directory up to repo_root looking for tsconfig.json or jsconfig.json.
    Returns the absolute path to the tsconfig file or None.
    """
    repo_root = Path(repo_root).resolve()
    file_dir = (repo_root / file_rel_path).resolve().parent
    for p in [file_dir] + list(file_dir.parents):
        if repo_root not in p.parents and p != repo_root and repo_root != p and repo_root not in (p,):
            # keep walking; condition mainly to ensure we don't ascend past repo root
            pass
        for name in ("tsconfig.json", "jsconfig.json"):
            candidate = p / name
            if candidate.exists():
                return str(candidate)
        if p == repo_root:
            break
    # finally check repo root explicitly
    for name in ("tsconfig.json", "jsconfig.json"):
        candidate = repo_root / name
        if candidate.exists():
            return str(candidate)
    return None

def resolve_path_alias(import_specifier: str, repo_root: str, base: Optional[str], paths: Dict[str, List[str]]) -> Optional[str]:
    """
    Try to map an import_specifier like "@/components/button" to a real file path
    using tsconfig paths. Returns a filesystem path if found, else None.
    """
    if not paths:
        return None

    spec = import_specifier
    for pattern, targets in paths.items():
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            if spec.startswith(prefix):
                suffix = spec[len(prefix):]
                for target in targets:
                    if target.endswith("*"):
                        target_prefix = target[:-1]
                    else:
                        target_prefix = target
                    candidate = os.path.normpath(os.path.join(repo_root, base or "", target_prefix + suffix))
                    for ext in ("", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"):
                        fp = candidate + ext
                        if os.path.exists(fp):
                            return fp
                    for index_try in ("/index.ts", "/index.tsx", "/index.js", "/index.jsx"):
                        fp = candidate + index_try
                        if os.path.exists(fp):
                            return fp
        else:
            if spec == pattern:
                for target in targets:
                    candidate = os.path.normpath(os.path.join(repo_root, base or "", target))
                    if os.path.exists(candidate):
                        return candidate
    return None

# --- node_modules / package.json resolver ---------------------------------

def _read_package_json(pkg_dir: str) -> dict | None:
    pj = os.path.join(pkg_dir, "package.json")
    if not os.path.exists(pj):
        return None
    try:
        return json.load(open(pj, "r", encoding="utf-8"))
    except Exception:
        return None

def resolve_node_module(import_specifier: str, repo_root: str) -> Optional[str]:
    """
    Try to resolve a bare import like 'react' or 'lucide-react/Icon' to an actual path
    under repo_root/node_modules. Returns a repo-relative path (posix) if found, else None.
    Supports scoped packages @org/pkg and subpath imports.
    """
    repo_root = Path(repo_root).resolve()
    # Split package and subpath
    if import_specifier.startswith("@"):
        # scoped: @org/pkg or @org/pkg/sub/path
        parts = import_specifier.split("/")
        if len(parts) < 2:
            return None
        pkg = "/".join(parts[:2])
        subpath_parts = parts[2:]
    else:
        parts = import_specifier.split("/")
        pkg = parts[0]
        subpath_parts = parts[1:]

    pkg_dir = repo_root / "node_modules" / pkg
    if not pkg_dir.exists():
        return None

    # try package.json "exports"/"types"/"typings"/"main"
    pkg_json = _read_package_json(str(pkg_dir))
    candidate_files = []

    # subpath import handling: try node_modules/<pkg>/<subpath> + extensions
    if subpath_parts:
        base = pkg_dir.joinpath(*subpath_parts)
        for ext in ("", ".ts", ".tsx", ".js", ".jsx", ".d.ts"):
            candidate_files.append(str(base) + ext)
        # index variants
        for ext in (".ts", ".tsx", ".js", ".jsx", ".d.ts"):
            candidate_files.append(os.path.join(str(base), "index" + ext))

    # If no subpath, try types/main/index
    if not subpath_parts:
        if pkg_json:
            for key in ("types", "typings", "main"):
                val = pkg_json.get(key)
                if val:
                    candidate_files.append(str((pkg_dir / val).resolve()))
        # fallback candidates
        for name in ("index.d.ts", "index.ts", "index.tsx", "index.js", "index.jsx"):
            candidate_files.append(str(pkg_dir / name))

    # If package.json has "exports" mapping and the import_specifier refers to a subpath, attempt a simple mapping
    if pkg_json and "exports" in pkg_json:
        exports = pkg_json["exports"]
        # only handle simple string export and top-level subpaths like './feature'
        if isinstance(exports, str) and not subpath_parts:
            candidate_files.append(str((pkg_dir / exports).resolve()))
        elif isinstance(exports, dict) and subpath_parts:
            # map "./subpath" or the exact subpath
            lookup = "./" + "/".join(subpath_parts)
            exported = exports.get(lookup) or exports.get(lookup + "/")
            if isinstance(exported, str):
                candidate_files.append(str((pkg_dir / exported).resolve()))

    # Try the candidates
    for fp in candidate_files:
        if os.path.exists(fp):
            try:
                rel = os.path.relpath(fp, start=str(repo_root)).replace(os.sep, "/")
                return rel
            except Exception:
                return fp

    return None

# --- regex fallback extraction for JS/TSX ---------------------------------

IMPORT_RE = re.compile(r"""(?m)^\s*(?:import\s+(?:[\s\S]+?)\s+from\s+['"](?P<spec>[^'"]+)['"]|require\(\s*['"](?P<req>[^'"]+)['"]\s*\))""")
EXPORT_RE = re.compile(r"""(?m)^\s*export\s+(?:default\s+)?(?:class|function|const|let|var)?\s*(?P<name>[A-Za-z0-9_]+)?""")
FUNC_RE = re.compile(r"""(?m)^\s*(?:function\s+(?P<fname>[A-Za-z0-9_]+)\s*\(|const\s+(?P<cname>[A-Za-z0-9_]+)\s*=\s*\(|export\s+function\s+(?P<ef>[A-Za-z0-9_]+)\s*\()""")

def heuristic_extract_js_symbols(source_text: str) -> Dict[str, List[str]]:
    """
    Return a best-effort dict with lists: imports, exports, functions
    """
    imports = []
    exports = []
    funcs = []
    for m in IMPORT_RE.finditer(source_text):
        spec = m.group("spec") or m.group("req")
        if spec:
            imports.append(spec)
    for m in EXPORT_RE.finditer(source_text):
        name = m.group("name")
        if name:
            exports.append(name)
    for m in FUNC_RE.finditer(source_text):
        name = m.group("fname") or m.group("cname") or m.group("ef")
        if name:
            funcs.append(name)
    return {"imports": imports, "exports": exports, "functions": funcs}

# --- unparsed file recorder (JSON fallback if no DB) -----------------------

UNPARSED_JSON = os.path.join(".cache", "unparsed_files.json")

def ensure_cache_dir():
    d = os.path.dirname(UNPARSED_JSON)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)

def record_unparsed_json(repository_id: str, file_path: str, raw_content: str, reason: str):
    """
    Append an entry to .cache/unparsed_files.json (simple JSON-lines or list).
    If you have a DB, replace this with DB inserts.
    """
    ensure_cache_dir()
    entry = {
        "repository_id": repository_id,
        "file_path": file_path,
        "reason": reason,
        "raw_content_snippet": raw_content[:2000],
    }
    with open(UNPARSED_JSON, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")

# --- stubs for bare npm packages (already provided but extended) ----------

_STUBS_ROOT = os.path.join(".stubs", "npm")

def _safe_pkg_dir(repo_root: str, pkg_name: str) -> str:
    """Return a normalized package stub directory path under .stubs/npm/<pkg>"""
    safe_name = pkg_name.replace("/", "__").replace("@", "")
    return os.path.join(repo_root, _STUBS_ROOT, safe_name)

def create_stub_for_package(repo_root: str, pkg_name: str, exported_names: Iterable[str]) -> str:
    """
    Write a small TypeScript file that exports the requested names so the
    repo builder can parse and resolve them. Returns the filesystem path
    of the written stub (absolute).
    """
    if not exported_names:
        exported_names = ["__placeholder"]
    pkg_dir = _safe_pkg_dir(repo_root, pkg_name)
    os.makedirs(pkg_dir, exist_ok=True)
    stub_path = os.path.join(pkg_dir, "index.ts")
    lines: List[str] = []
    for name in sorted(set(exported_names)):
        if not name:
            continue
        # heuristics: hooks (useX) => function, Components/icons (Start Uppercase) => function,
        # otherwise const placeholder.
        if name.startswith("use") and len(name) > 3 and name[3].isalpha():
            lines.append(f"export function {name}(..._args: any[]): any {{ return undefined; }}")
        elif name[0].isupper():
            lines.append(f"export function {name}(_props?: any): any {{ return null; }}")
        else:
            # fall back to const for non-identifier or plain values
            if name and name[0].isidentifier():
                lines.append(f"export const {name}: any = undefined;")
            else:
                lines.append(f"// skipped invalid export name: {name}")
    lines.append("\nexport default {} as any;")
    with open(stub_path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return os.path.abspath(stub_path)
def create_stubs_for_bare_packages(repo_root: str, file_results: Iterable) -> Dict[str, str]:
    """
    Collect bare specifier imports across file_results and create stubs under .stubs/npm.
    Returns a dict pkg_name -> stub_rel_path (repo-relative, posix).
    """
    pkg_to_names: Dict[str, Set[str]] = {}
    for fr in file_results:
        if getattr(fr, "language_family", "") != "js":
            continue
        for imp in getattr(fr.extracted, "imports", []):
            mod = getattr(imp, "module", "")
            if not mod or mod.startswith("."):
                continue
            names = getattr(imp, "imported_names", []) or []
            pkg_to_names.setdefault(mod, set()).update(names)

    pkg_to_stub_rel: Dict[str, str] = {}
    for pkg, names in pkg_to_names.items():
        stub_fs = create_stub_for_package(str(repo_root), pkg, names)
        try:
            rel = os.path.relpath(stub_fs, start=str(repo_root)).replace(os.sep, "/")
        except Exception:
            rel = stub_fs
        pkg_to_stub_rel[pkg] = rel
    return pkg_to_stub_rel