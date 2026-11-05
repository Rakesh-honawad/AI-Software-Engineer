"""
storage/repository_profile/entry_point_frameworks.py

Generalizes entry-point detection from hardcoded FastAPI/Flask/Next.js
logic into a data-driven registry. Fixes a real, confirmed-live bug:
a Next.js frontend repo showed 310/310 dead-code candidates unreached
because the previous detector only knew Python/Express backend
conventions (FastAPI(/Flask(/express()) — zero awareness that
page.tsx/layout.tsx/route.ts files are invoked by Next.js's own
router, never by an explicit call anywhere in the repo's own code.

SCOPE, STATED PLAINLY: this only covers frameworks built on languages
the parser already supports (Python, JS/TS/JSX/TSX — see
graph/builder/repo_builder.py's LANGUAGE_DISPATCH). A framework in a
language with no tree-sitter grammar/extractor yet (Go, Rust, Java,
Vue SFC .vue files, etc.) can't be covered by adding a rule here —
that needs a new parser first, a separate, larger project. Within
that real boundary, this file's whole point is: adding support for a
new Python or JS/TS framework should NEVER require touching detection
logic again — just add one FrameworkEntryPointRule below.

HOW A RULE WORKS:
  - dependency_markers: if any of these appear in the repo's detected
    dependencies (package.json/requirements.txt/pyproject.toml), this
    framework is considered "in use" — rules with no markers (like
    plain Next.js App Router file conventions) always apply.
  - special_filenames (+ optional required parent dir): exact
    filenames that are ALWAYS framework-invoked regardless of content
    (e.g. Next.js's page.tsx, Django's wsgi.py).
  - route_dir_names: any source file directly under one of these
    directory names counts as an entry point (e.g. Next.js Pages
    Router's pages/, Remix's app/routes/).
  - app_instantiation_patterns: content substrings indicating "this
    file creates the framework's app object" (e.g. "FastAPI(").
  - decorator_or_call_patterns: content substrings indicating a route/
    handler/task registration (e.g. "@app.get(", "app.get(", "@Get(",
    "@shared_task").
Content-pattern files are still scanned only within a bounded set of
plausibly-relevant filenames/directories — same "good enough signal,
not AST-perfect" philosophy as the original detector, still not
false-positive-free, still surfaced via the existing `caveat` field.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".pytest_cache", ".mypy_cache",
    "coverage", ".tox", "target", ".idea", ".vscode", "egg-info",
}

_CONTENT_SCAN_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".jsx"}


@dataclass(frozen=True)
class FrameworkEntryPointRule:
    name: str
    dependency_markers: frozenset[str] = frozenset()
    special_filenames: frozenset[str] = frozenset()
    special_filenames_require_parent_dir: str | None = None
    route_dir_names: frozenset[str] = frozenset()
    route_dir_suffixes: frozenset[str] = field(
        default_factory=lambda: frozenset({".py", ".ts", ".tsx", ".js", ".jsx"})
    )
    app_instantiation_patterns: tuple[str, ...] = ()
    decorator_or_call_patterns: tuple[str, ...] = ()
    # Only scan file contents for pattern matches if the filename stem
    # is one of these OR the file sits under a route_dir — keeps this
    # from reading every source file in a large repo. None = scan any
    # file with a supported suffix (used sparingly, only for very
    # distinctive patterns like "FastAPI(").
    content_scan_stems: frozenset[str] | None = None


# ── THE REGISTRY — add a new framework by adding one rule here ──────
FRAMEWORK_RULES: tuple[FrameworkEntryPointRule, ...] = (
    FrameworkEntryPointRule(
        name="fastapi",
        dependency_markers=frozenset({"fastapi"}),
        app_instantiation_patterns=("FastAPI(",),
        decorator_or_call_patterns=(
            "@app.get(", "@app.post(", "@app.put(", "@app.delete(", "@app.patch(",
            "@router.get(", "@router.post(", "@router.put(", "@router.delete(", "@router.patch(",
        ),
        content_scan_stems=frozenset({"main", "app", "server", "asgi", "wsgi"}),
    ),
    FrameworkEntryPointRule(
        name="flask",
        dependency_markers=frozenset({"flask"}),
        app_instantiation_patterns=("Flask(__name__", "Flask("),
        decorator_or_call_patterns=("@app.route(",),
        content_scan_stems=frozenset({"main", "app", "server", "wsgi"}),
    ),
    FrameworkEntryPointRule(
        name="django",
        dependency_markers=frozenset({"django"}),
        special_filenames=frozenset({"urls.py", "wsgi.py", "asgi.py", "manage.py"}),
    ),
    FrameworkEntryPointRule(
        name="celery",
        dependency_markers=frozenset({"celery"}),
        decorator_or_call_patterns=("@app.task(", "@shared_task", "@celery_app.task("),
        content_scan_stems=frozenset({"tasks", "worker", "celery_app", "jobs"}),
    ),
    FrameworkEntryPointRule(
        name="nextjs_app_router",
        dependency_markers=frozenset({"next"}),
        special_filenames=frozenset({
            "page.tsx", "page.ts", "page.jsx", "page.js",
            "layout.tsx", "layout.ts", "layout.jsx", "layout.js",
            "template.tsx", "template.ts",
            "default.tsx", "default.ts",
            "loading.tsx", "loading.ts",
            "error.tsx", "error.ts",
            "not-found.tsx", "not-found.ts",
            "route.ts", "route.tsx",
        }),
        special_filenames_require_parent_dir="app",
    ),
    FrameworkEntryPointRule(
        name="nextjs_pages_router",
        dependency_markers=frozenset({"next"}),
        route_dir_names=frozenset({"pages"}),
    ),
    FrameworkEntryPointRule(
        name="nextjs_middleware",
        dependency_markers=frozenset({"next"}),
        special_filenames=frozenset({"middleware.ts", "middleware.js"}),
        # no parent-dir requirement — must be checked at repo root by the caller
    ),
    FrameworkEntryPointRule(
        name="express",
        dependency_markers=frozenset({"express"}),
        app_instantiation_patterns=("express()",),
        decorator_or_call_patterns=(
            "app.get(", "app.post(", "app.put(", "app.delete(", "app.patch(",
            "router.get(", "router.post(", "router.put(", "router.delete(", "router.patch(",
        ),
        content_scan_stems=frozenset({"main", "app", "server", "index"}),
    ),
    FrameworkEntryPointRule(
        name="nestjs",
        dependency_markers=frozenset({"@nestjs/core"}),
        decorator_or_call_patterns=(
            "@Controller(", "@Get(", "@Post(", "@Put(", "@Delete(", "@Patch(",
            "@EventPattern(", "@MessagePattern(", "@Cron(",
        ),
        content_scan_stems=None,  # NestJS spreads controllers across many files by convention
    ),
    FrameworkEntryPointRule(
        name="fastify",
        dependency_markers=frozenset({"fastify"}),
        app_instantiation_patterns=("fastify()", "Fastify()"),
        decorator_or_call_patterns=("fastify.get(", "fastify.post(", "app.get(", "app.post("),
        content_scan_stems=frozenset({"main", "app", "server", "index"}),
    ),
    FrameworkEntryPointRule(
        name="remix",
        dependency_markers=frozenset({"@remix-run/node", "@remix-run/react", "@remix-run/serve"}),
        route_dir_names=frozenset({"routes"}),
    ),
)


def _read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def detect_framework_entry_points(repo_path: Path, dependency_names: set[str]) -> list[str]:
    """
    Returns relative paths (forward-slash) of files considered entry
    points by any applicable framework rule. `dependency_names` should
    be lowercase, as produced by tier1_generator.py's existing
    dependency detection — a rule with dependency_markers only applies
    if at least one marker is present; a rule with no markers always
    applies (e.g. bare file-convention checks that don't need a
    package.json entry to be meaningful).
    """
    applicable_rules = [
        rule for rule in FRAMEWORK_RULES
        if not rule.dependency_markers or (rule.dependency_markers & dependency_names)
    ]
    if not applicable_rules:
        return []

    found: list[str] = []
    seen: set[str] = set()

    def add(rel_path: str) -> None:
        if rel_path not in seen:
            found.append(rel_path)
            seen.add(rel_path)

    for path in repo_path.rglob("*"):
        if not path.is_file() or any(part in IGNORE_DIRS for part in path.parts):
            continue
        rel = str(path.relative_to(repo_path)).replace("\\", "/")
        parts = path.relative_to(repo_path).parts

        for rule in applicable_rules:
            # 1. Exact special filenames (with optional required parent dir)
            if path.name in rule.special_filenames:
                if rule.special_filenames_require_parent_dir is None or (
                    rule.special_filenames_require_parent_dir in parts
                ):
                    add(rel)
                    continue

            # 2. Anything directly under a route directory
            if rule.route_dir_names and path.suffix in rule.route_dir_suffixes:
                if any(d in parts for d in rule.route_dir_names):
                    add(rel)
                    continue

            # 3. Content-based scan (app instantiation / decorator-or-call patterns)
            if not (rule.app_instantiation_patterns or rule.decorator_or_call_patterns):
                continue
            if path.suffix not in _CONTENT_SCAN_SUFFIXES:
                continue
            if rule.content_scan_stems is not None and path.stem not in rule.content_scan_stems:
                # Still allow content scan for files whose stem doesn't
                # match IF the rule has no stem restriction at all
                # (content_scan_stems=None means "scan broadly") —
                # handled by the outer None-check below already; this
                # branch only skips when a restriction exists and isn't met.
                continue

            text = _read_text(path)
            if text is None:
                continue
            patterns = rule.app_instantiation_patterns + rule.decorator_or_call_patterns
            if any(p in text for p in patterns):
                add(rel)

    return found