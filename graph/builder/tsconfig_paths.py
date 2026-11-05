"""
Additions to graph/builder/repo_builder.py: resolves tsconfig.json /
jsconfig.json path aliases (e.g. "@/*" -> "./*", the standard Next.js/
shadcn convention) — previously ONLY literal relative imports ("./x",
"../x") resolved at all; anything starting with "@/" fell straight
into unresolved, indistinguishable from a real npm package. Confirmed
live: this was undercounting reachability/impact across an entire
real Next.js repo (11/310 reachable before this fix, `cn` in
lib/utils.ts showing 165 dead-code-candidate callers yet itself
appearing dead — a direct symptom of every "@/lib/utils" import
silently failing to resolve).

SCOPE: reads `compilerOptions.paths` + `compilerOptions.baseUrl` (baseUrl
defaults to the tsconfig's own directory when absent — matches real
Next.js/tsc behavior with "moduleResolution": "bundler"). Supports the
common single-wildcard pattern (`"@/*": ["./*"]`) and exact,
non-wildcard aliases. Does NOT support multiple fallback paths per
alias (`"@/*": ["./src/*", "./lib/*"]` — only the first entry is
tried, same "common case first" pragmatism as the rest of this
project) or nested/scoped tsconfig "references" (monorepo project
references) — those are real, rarer setups, left for later if they
come up.
"""

from __future__ import annotations

import json
import re
from pathlib import Path


def _load_tsconfig_paths(repo_path: Path) -> list[tuple[str, str]]:
    """
    Returns [(alias_prefix, target_prefix), ...] pairs, e.g.
    [("@/", "")] for {"@/*": ["./*"]} with baseUrl at repo root.
    Sorted longest-alias-first so a more specific alias (e.g.
    "@/components/*") is tried before a broader one (e.g. "@/*").
    Returns [] if no tsconfig.json/jsconfig.json, or no `paths` config.
    """
    config_path = repo_path / "tsconfig.json"
    if not config_path.exists():
        config_path = repo_path / "jsconfig.json"
        if not config_path.exists():
            return []

    try:
        # tsconfig.json commonly contains // comments and trailing
        # commas, which json.loads rejects — strip // line comments
        # (simple, not a full JSONC parser, but handles the
        # overwhelming common case without adding a dependency).
        raw = config_path.read_text(encoding="utf-8", errors="ignore")
        raw = re.sub(r"(?<!:)//.*", "", raw)  # avoid stripping "https://" inside strings
        raw = re.sub(r",(\s*[}\]])", r"\1", raw)  # trailing commas
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError):
        return []

    compiler_options = data.get("compilerOptions", {})
    paths = compiler_options.get("paths", {})
    if not paths:
        return []

    base_url = compiler_options.get("baseUrl", ".")
    base_prefix = "" if base_url in (".", "./") else base_url.rstrip("/") + "/"

    pairs: list[tuple[str, str]] = []
    for alias_pattern, targets in paths.items():
        if not targets:
            continue
        target_pattern = targets[0]  # first entry only, see module docstring

        if alias_pattern.endswith("*") and target_pattern.endswith("*"):
            alias_prefix = alias_pattern[:-1]  # "@/*" -> "@/"
            target_prefix = target_pattern[:-1].removeprefix("./")  # "./*" -> ""
            pairs.append((alias_prefix, base_prefix + target_prefix))
        elif "*" not in alias_pattern and "*" not in target_pattern:
            # exact, non-wildcard alias
            pairs.append((alias_pattern, base_prefix + target_pattern.removeprefix("./")))
        # patterns with "*" in a non-trailing position are rare/unusual
        # enough to skip rather than mis-resolve — same "don't guess
        # the hard ones" principle as everywhere else in this project.

    pairs.sort(key=lambda pair: len(pair[0]), reverse=True)
    return pairs


def _resolve_via_path_alias(
    import_module: str, path_aliases: list[tuple[str, str]], known_file_paths: set[str],
    js_resolution_suffixes: list[str],
) -> str | None:
    """Tries each configured path alias against import_module (e.g.
    "@/lib/utils"); on a prefix match, substitutes and resolves the
    same way a relative import resolves (as-is, +suffix, +/index+suffix).
    Returns None if import_module doesn't match any configured alias —
    caller should then try relative-import resolution as normal."""
    for alias_prefix, target_prefix in path_aliases:
        if not import_module.startswith(alias_prefix):
            continue
        candidate_base = target_prefix + import_module[len(alias_prefix):]
        candidate_base = candidate_base.strip("/")

        if candidate_base in known_file_paths:
            return candidate_base
        for suffix in js_resolution_suffixes:
            if f"{candidate_base}{suffix}" in known_file_paths:
                return f"{candidate_base}{suffix}"
        for suffix in js_resolution_suffixes:
            if f"{candidate_base}/index{suffix}" in known_file_paths:
                return f"{candidate_base}/index{suffix}"
        return None  # matched an alias prefix but target file not found — don't fall through to relative resolution with the ORIGINAL "@/..." string, that would never resolve either

    return None