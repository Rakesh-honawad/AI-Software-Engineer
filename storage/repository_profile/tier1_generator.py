from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from .types import (
    CodingStyle,
    FrameworkInfo,
    IndexStatus,
    ProfileSource,
    RepositoryCapabilities,
    RepositoryProfile,
    RepositoryStats,
    VCSInfo,
)

IGNORE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "env",
    "dist", "build", ".next", ".pytest_cache", ".mypy_cache",
    "coverage", ".tox", "target", ".idea", ".vscode", "egg-info",
}

LANGUAGE_BY_EXTENSION = {
    ".py": "Python", ".pyi": "Python",
    ".ts": "TypeScript", ".tsx": "TypeScript",
    ".js": "JavaScript", ".jsx": "JavaScript", ".mjs": "JavaScript",
    ".go": "Go", ".rs": "Rust", ".java": "Java", ".kt": "Kotlin",
    ".rb": "Ruby", ".php": "PHP", ".c": "C", ".h": "C",
    ".cpp": "C++", ".hpp": "C++", ".cs": "C#", ".swift": "Swift",
    ".sql": "SQL", ".sh": "Shell", ".ps1": "PowerShell",
}

KNOWN_FRAMEWORKS = {
    "fastapi": ("FastAPI", "web"),
    "django": ("Django", "web"),
    "flask": ("Flask", "web"),
    "express": ("Express", "web"),
    "next": ("Next.js", "frontend"),
    "react": ("React", "frontend"),
    "vue": ("Vue", "frontend"),
    "@angular/core": ("Angular", "frontend"),
    "svelte": ("Svelte", "frontend"),
    "sqlalchemy": ("SQLAlchemy", "orm"),
    "prisma": ("Prisma", "orm"),
    "@prisma/client": ("Prisma", "orm"),
    "mongoose": ("Mongoose", "orm"),
    "typeorm": ("TypeORM", "orm"),
    "pytest": ("pytest", "testing"),
    "jest": ("Jest", "testing"),
    "mocha": ("Mocha", "testing"),
    "vitest": ("Vitest", "testing"),
    "celery": ("Celery", "task_queue"),
    "bullmq": ("BullMQ", "task_queue"),
    "bull": ("Bull", "task_queue"),
    "rq": ("RQ", "task_queue"),
}

DATABASE_MARKERS = {
    "psycopg2": "PostgreSQL", "psycopg2-binary": "PostgreSQL", "asyncpg": "PostgreSQL",
    "pg": "PostgreSQL", "sqlalchemy": "PostgreSQL/SQL",
    "redis": "Redis", "ioredis": "Redis",
    "pymongo": "MongoDB", "mongoose": "MongoDB", "motor": "MongoDB",
    "mysqlclient": "MySQL", "mysql2": "MySQL", "pymysql": "MySQL",
    "sqlite3": "SQLite", "better-sqlite3": "SQLite",
    "kuzu": "Kuzu",
}

BACKGROUND_JOB_MARKERS = {"celery", "bullmq", "bull", "rq", "arq", "dramatiq"}
API_FRAMEWORK_MARKERS = {"fastapi", "django", "flask", "express", "koa", "nestjs", "@nestjs/core"}
TEST_FRAMEWORK_MARKERS = {"pytest": "pytest", "jest": "Jest", "mocha": "Mocha", "vitest": "Vitest", "unittest": "unittest"}


def _read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _parse_package_json(repo_path: Path) -> dict[str, str]:
    pkg = _read_json(repo_path / "package.json")
    deps: dict[str, str] = {}
    deps.update(pkg.get("dependencies", {}) or {})
    deps.update(pkg.get("devDependencies", {}) or {})
    return deps


def _parse_requirements_txt(repo_path: Path) -> dict[str, str]:
    req_file = repo_path / "requirements.txt"
    deps: dict[str, str] = {}
    if not req_file.exists():
        return deps
    for line in req_file.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("-"):
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)\s*([><=!~].*)?$", line)
        if match:
            deps[match.group(1).lower()] = (match.group(2) or "").strip()
    return deps


def _parse_pyproject_toml(repo_path: Path) -> dict[str, str]:
    pp_file = repo_path / "pyproject.toml"
    deps: dict[str, str] = {}
    if not pp_file.exists():
        return deps
    text = pp_file.read_text(encoding="utf-8", errors="ignore")

    array_match = re.search(r"(?<!optional-)dependencies\s*=\s*\[(.*?)\]", text, re.DOTALL)
    if array_match:
        for entry in re.finditer(r'"([A-Za-z0-9_.\-]+)\s*([><=!~][^"]*)?"', array_match.group(1)):
            deps[entry.group(1).lower()] = (entry.group(2) or "").strip()

    poetry_match = re.search(
        r"\[tool\.poetry\.dependencies\](.*?)(?=\n\[|\Z)", text, re.DOTALL
    )
    if poetry_match:
        for line in poetry_match.group(1).splitlines():
            kv_match = re.match(r'\s*([A-Za-z0-9_.\-]+)\s*=\s*"?([^"\n]*)"?', line)
            if kv_match:
                name = kv_match.group(1).lower()
                if name == "python":
                    continue
                deps[name] = kv_match.group(2).strip()

    return deps


def _detect_dependencies(repo_path: Path) -> tuple[int, list[str], dict[str, str]]:
    all_deps: dict[str, str] = {}
    all_deps.update(_parse_package_json(repo_path))
    all_deps.update(_parse_requirements_txt(repo_path))
    all_deps.update(_parse_pyproject_toml(repo_path))
    names = list(all_deps.keys())
    return len(names), names[:10], all_deps


def _detect_languages(repo_path: Path) -> tuple[str, dict[str, float]]:
    counts: dict[str, int] = {}
    for path in repo_path.rglob("*"):
        if not path.is_file():
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        lang = LANGUAGE_BY_EXTENSION.get(path.suffix.lower())
        if lang:
            counts[lang] = counts.get(lang, 0) + 1

    total = sum(counts.values())
    if total == 0:
        return "", {}

    languages = {lang: round(count / total, 3) for lang, count in counts.items()}
    primary = max(languages, key=languages.get)
    return primary, languages


def _detect_frameworks(dep_names_lower: dict[str, str]) -> list[FrameworkInfo]:
    frameworks = []
    for dep_name, version in dep_names_lower.items():
        if dep_name in KNOWN_FRAMEWORKS:
            display_name, category = KNOWN_FRAMEWORKS[dep_name]
            frameworks.append(FrameworkInfo(
                name=display_name,
                category=category,
                version=version or None,
                confidence=1.0,
                source=ProfileSource.DETECTED,
            ))
    return frameworks


def _detect_database_systems(dep_names: set[str]) -> list[str]:
    found = {DATABASE_MARKERS[name] for name in dep_names if name in DATABASE_MARKERS}
    return sorted(found)


def _detect_capabilities(repo_path: Path, dep_names: set[str]) -> RepositoryCapabilities:
    def exists_any(*candidates: str) -> bool:
        return any((repo_path / c).exists() for c in candidates)

    has_tests = exists_any("tests", "test", "__tests__", "spec") or any(
        p.name.startswith("test_") or p.name.endswith("_test.py") or p.name.endswith(".test.js")
        or p.name.endswith(".test.ts") or p.name.endswith(".spec.ts")
        for p in repo_path.rglob("*")
        if p.is_file() and not any(part in IGNORE_DIRS for part in p.parts)
    )

    has_frontend = exists_any("frontend", "client", "web") or bool(
        {"react", "vue", "@angular/core", "svelte", "next"} & dep_names
    )

    return RepositoryCapabilities(
        has_api=bool(API_FRAMEWORK_MARKERS & dep_names) or exists_any("routes", "api", "endpoints"),
        has_database=bool(set(DATABASE_MARKERS) & dep_names) or exists_any("migrations", "models"),
        has_tests=has_tests,
        has_frontend=has_frontend,
        has_background_jobs=bool(BACKGROUND_JOB_MARKERS & dep_names),
        has_ci=exists_any(".github/workflows", ".gitlab-ci.yml", ".circleci"),
        has_docker=exists_any("Dockerfile", "docker-compose.yml", "docker-compose.yaml"),
        has_kubernetes=exists_any("k8s", "kubernetes", "helm", "charts"),
    )


def _detect_build_and_package_manager(repo_path: Path) -> tuple[str | None, str | None]:
    if (repo_path / "pyproject.toml").exists():
        build_system = "pyproject.toml"
        package_manager = "poetry" if "poetry" in (repo_path / "pyproject.toml").read_text(errors="ignore") else "pip"
    elif (repo_path / "setup.py").exists():
        build_system, package_manager = "setup.py", "pip"
    elif (repo_path / "requirements.txt").exists():
        build_system, package_manager = "requirements.txt", "pip"
    elif (repo_path / "package.json").exists():
        build_system = "package.json"
        if (repo_path / "pnpm-lock.yaml").exists():
            package_manager = "pnpm"
        elif (repo_path / "yarn.lock").exists():
            package_manager = "yarn"
        else:
            package_manager = "npm"
    else:
        build_system, package_manager = None, None
    return build_system, package_manager


def _detect_testing_framework(dep_names: set[str]) -> str | None:
    for marker, display in TEST_FRAMEWORK_MARKERS.items():
        if marker in dep_names:
            return display
    return None


# FIXED — was a short hardcoded guess-list that only covered a few
# fixed layouts (e.g. "backend/app/main.py", which only matches when
# repo_path is one level ABOVE a backend/ folder — never when
# backend/ itself IS the indexed repo_path, which is exactly what
# happened indexing this project's own backend/: the real file at
# backend/app/main.py is just "app/main.py" relative to the indexed
# root, and that exact string was never in the old candidate list).
# Two passes now:
#   1. A wider static candidate list — still just existence checks,
#      still fast, but covers many more common layouts.
#   2. A content-based scan for files that actually instantiate a
#      known web framework's app object (FastAPI(), Flask(...),
#      express()). This is what makes detection layout-independent
#      instead of hardcoding directory-name guesses forever — any
#      "app/main.py", "src/server.py", "api/asgi.py", etc. that
#      genuinely instantiates the app gets found, without needing to
#      enumerate every possible path up front.
_STATIC_ENTRY_CANDIDATES = (
    "main.py", "app.py", "manage.py", "asgi.py", "wsgi.py", "server.py",
    "src/main.py", "src/app.py", "src/server.py",
    "app/main.py", "app/app.py",
    "backend/main.py", "backend/app.py", "backend/app/main.py",
    "api/main.py",
    "index.js", "index.ts", "server.js", "server.ts",
    "src/index.js", "src/index.ts", "src/server.js", "src/server.ts",
)

# Simple substring match, not AST parsing — this only needs to be
# right often enough to be a useful signal, not perfect. A false
# positive just adds one extra entry-point candidate (dead-code
# analysis only ever uses these as possible roots, never as a
# guarantee); a false negative just means this content scan found
# nothing and the static list above is all that's used.
_ENTRY_POINT_APP_PATTERNS = ("FastAPI(", "Flask(__name__", "Flask(", "express()")

# Content scan is restricted to files with one of these stems, so this
# doesn't read every .py/.js/.ts file's contents in a large repo —
# only ones already named like a plausible entry point.
_ENTRY_POINT_SCAN_STEMS = {"main", "app", "server", "asgi", "wsgi", "index"}
_ENTRY_POINT_SCAN_SUFFIXES = {".py", ".js", ".ts"}

# SECOND PASS — added after a real gap found in practice: the first
# pass (static candidates + app-instantiation content scan) only ever
# finds the ONE file that calls FastAPI()/Flask()/express(). It has
# no way to find the files that actually DEFINE routes when a project
# splits handlers into separate router files (e.g. FastAPI's
# APIRouter pattern: app/api/v1/routes/chat.py, wired into main.py
# via app.include_router(...)) — a completely standard, common layout
# this scanner was blind to. Route decorators still aren't parsed
# into the GRAPH (that's frameworks/, still Phase 2 — see
# dead_code_analysis.py's caveat), but at the entry-points-list level
# we CAN reliably catch these: scan files under a conventionally-named
# routes/API directory for actual decorator syntax, and if found,
# treat the whole file as an entry point (its top-level handler
# functions are what's externally invoked).
_ROUTE_DIR_HINTS = {"routes", "route", "api", "endpoints", "views", "controllers"}
_ROUTE_DECORATOR_PATTERNS = (
    "@app.get(", "@app.post(", "@app.put(", "@app.delete(", "@app.patch(", "@app.route(",
    "@router.get(", "@router.post(", "@router.put(", "@router.delete(", "@router.patch(", "@router.route(",
)


def _detect_entry_points(repo_path: Path) -> list[str]:
    entry_points: list[str] = []
    pkg = _read_json(repo_path / "package.json")
    if pkg.get("main"):
        entry_points.append(pkg["main"])
    for script_name in ("start", "dev"):
        if script_name in (pkg.get("scripts") or {}):
            entry_points.append(f"npm run {script_name}")

    for candidate in _STATIC_ENTRY_CANDIDATES:
        if (repo_path / candidate).exists():
            entry_points.append(candidate)

    seen = set(entry_points)

    # Pass 1: files named like a plausible app-instantiation entry
    # point (main.py, app.py, ...) that actually instantiate one.
    for path in repo_path.rglob("*"):
        if not path.is_file() or path.suffix not in _ENTRY_POINT_SCAN_SUFFIXES:
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        rel = str(path.relative_to(repo_path)).replace("\\", "/")
        if rel in seen:
            continue
        if path.stem in _ENTRY_POINT_SCAN_STEMS:
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(pattern in text for pattern in _ENTRY_POINT_APP_PATTERNS):
                entry_points.append(rel)
                seen.add(rel)
                continue

        # Pass 2: files under a routes/api/endpoints/views/controllers
        # directory that actually contain a route decorator — this is
        # what catches split-out router files like app/api/v1/routes/chat.py.
        if path.suffix == ".py" and any(part.lower() in _ROUTE_DIR_HINTS for part in path.parts):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if any(pattern in text for pattern in _ROUTE_DECORATOR_PATTERNS):
                entry_points.append(rel)
                seen.add(rel)

    return entry_points


def _detect_coding_style(repo_path: Path) -> CodingStyle:
    detected_from: list[str] = []
    linter = formatter = None
    quote_style = None
    line_length = None

    if (repo_path / ".eslintrc.json").exists() or (repo_path / ".eslintrc.js").exists():
        linter = "eslint"
        detected_from.append(".eslintrc")
    if (repo_path / ".prettierrc").exists() or (repo_path / ".prettierrc.json").exists():
        formatter = "prettier"
        detected_from.append(".prettierrc")

    pp_file = repo_path / "pyproject.toml"
    if pp_file.exists():
        text = pp_file.read_text(errors="ignore")
        if "[tool.black]" in text:
            formatter = "black"
            detected_from.append("pyproject.toml [tool.black]")
            ll_match = re.search(r"line-length\s*=\s*(\d+)", text)
            if ll_match:
                line_length = int(ll_match.group(1))
        if "[tool.ruff]" in text:
            linter = "ruff"
            detected_from.append("pyproject.toml [tool.ruff]")

    return CodingStyle(
        quote_style=quote_style,
        line_length=line_length,
        linter=linter,
        formatter=formatter,
        detected_from=detected_from,
    )


def _run_git(repo_path: Path, *args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], cwd=repo_path, capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _find_git_root(start_path: Path) -> Path | None:
    """Walk upward from start_path looking for a .git directory, the same
    way `git` itself resolves the repo root from any subdirectory. This is
    what lets VCS detection work correctly even when generate_tier1_profile
    is pointed at a monorepo subproject (e.g. backend/) rather than the
    true repository root — the manifest scan stays scoped to start_path,
    but VCS info correctly resolves to the shared .git regardless.
    """
    current = start_path.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _detect_vcs(repo_path: Path) -> VCSInfo | None:
    git_root = _find_git_root(repo_path)
    if git_root is None:
        return None

    branch = _run_git(git_root, "rev-parse", "--abbrev-ref", "HEAD") or "unknown"
    commit = _run_git(git_root, "rev-parse", "HEAD") or "unknown"
    remote = _run_git(git_root, "remote", "get-url", "origin")
    status = _run_git(git_root, "status", "--porcelain")
    default_branch_raw = _run_git(git_root, "symbolic-ref", "refs/remotes/origin/HEAD")
    default_branch = default_branch_raw.rsplit("/", 1)[-1] if default_branch_raw else branch

    return VCSInfo(
        branch=branch,
        default_branch=default_branch,
        last_indexed_commit=commit,
        remote_url=remote,
        is_dirty=bool(status),
    )


def _detect_basic_stats(repo_path: Path) -> RepositoryStats:
    file_count = 0
    line_count = 0
    for path in repo_path.rglob("*"):
        if not path.is_file() or any(part in IGNORE_DIRS for part in path.parts):
            continue
        file_count += 1
        if path.suffix.lower() in LANGUAGE_BY_EXTENSION:
            try:
                line_count += sum(1 for _ in path.open(encoding="utf-8", errors="ignore"))
            except OSError:
                pass
    return RepositoryStats(files=file_count, lines_of_code=line_count)


_TIER1_COMPLETENESS_CHECKS = (
    "primary_language", "languages", "frameworks", "entry_points",
    "database_systems", "dependency_count", "build_system",
    "package_manager", "vcs",
)


def _compute_tier1_completeness(profile: RepositoryProfile) -> float:
    filled = 0
    for field_name in _TIER1_COMPLETENESS_CHECKS:
        value = getattr(profile, field_name)
        if value not in (None, "", [], {}, 0):
            filled += 1
    return round(filled / len(_TIER1_COMPLETENESS_CHECKS), 3)


def generate_tier1_profile(repo_path: str | Path, repository_id: str) -> RepositoryProfile:
    repo_path = Path(repo_path).resolve()
    if not repo_path.is_dir():
        raise ValueError(f"repo_path does not exist or is not a directory: {repo_path}")

    name = repo_path.name
    primary_language, languages = _detect_languages(repo_path)
    dep_count, key_deps, all_deps = _detect_dependencies(repo_path)
    dep_names = set(all_deps.keys())

    profile = RepositoryProfile(
        repository_id=repository_id,
        name=name,
        index_status=IndexStatus.INDEXING,
        primary_language=primary_language,
        languages=languages,
        frameworks=_detect_frameworks(all_deps),
        entry_points=_detect_entry_points(repo_path),
        database_systems=_detect_database_systems(dep_names),
        capabilities=_detect_capabilities(repo_path, dep_names),
        dependency_count=dep_count,
        key_dependencies=key_deps,
        coding_style=_detect_coding_style(repo_path),
        testing_framework=_detect_testing_framework(dep_names),
        stats=_detect_basic_stats(repo_path),
        vcs=_detect_vcs(repo_path),
    )
    profile.build_system, profile.package_manager = _detect_build_and_package_manager(repo_path)
    profile.completeness = _compute_tier1_completeness(profile)

    return profile