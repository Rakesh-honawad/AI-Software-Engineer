"""
sandbox/runner.py

Applies a model-generated unified diff to a DISPOSABLE COPY of a repo and
runs tests there. The original checkout is never modified.

Verification rules (v2):
  * Outcomes come from pytest's JUnit XML, not from scraping console text.
    Skipped / deselected / deleted tests can therefore never count as fixed.
  * When a generated reproduction test is supplied (extra_files), "fixed"
    requires THAT test to fail before the patch and pass after it.
    An unrelated test flipping to green proves nothing.
  * Every test that passed before must still pass after (regression check).
  * The patch guard rejects diffs that touch pytest config / conftest.py,
    the reproduction test itself, delete or rename tests, or add skip/xfail.
  * Anything we cannot prove is reported as verification_unavailable,
    never as fixed.

Safety note: a temp dir + subprocess with a stripped environment is NOT a
hard security boundary (no network/CPU/memory isolation). Fine for a local
demo; use a container for anything hosted.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

IGNORE = shutil.ignore_patterns(
    ".git", ".venv", "venv", "node_modules", "__pycache__", ".pytest_cache", ".mypy_cache",".env", ".env.*", ".aws", ".ssh", ".netrc", ".npmrc", ".pypirc",".git-credentials", "*.pem", "*.key", "*.p12", "id_rsa", "id_ed25519",

)
MAX_LOG_CHARS = 8000
DEFAULT_TEST_CMD = [sys.executable, "-m", "pytest", "-q", "--tb=short", "--continue-on-collection-errors"]

# Files a patch must never touch: they can change how tests are collected or judged.
PROTECTED_FILES = {"pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini", "conftest.py", ".coveragerc"}

BAD = ("failed", "error")

# verdict values
V_FIXED = "fixed"
V_NOT_FIXED = "not_fixed"
V_REGRESSION = "regression"
V_NOT_REPRODUCED = "not_reproduced"
V_UNAVAILABLE = "verification_unavailable"


@dataclass
class CommandResult:
    command: list[str]
    exit_code: int | None      # None = timed out or failed to start
    output: str
    duration_s: float
    timed_out: bool = False
    failed_tests: list[str] = field(default_factory=list)    # ids pytest printed as FAILED/ERROR (display only)
    outcomes: dict[str, str] = field(default_factory=dict)   # "module::test" -> passed|failed|error|skipped
    junit_valid: bool = False
    collection_errors: bool = False


@dataclass
class VerificationResult:
    patch_applied: bool
    apply_log: str = ""
    baseline: CommandResult | None = None   # tests BEFORE the patch
    patched: CommandResult | None = None    # tests AFTER the patch
    fixed: bool = False                     # reported bug proven fixed, nothing broken
    verdict: str = ""                       # fixed | not_fixed | regression | not_reproduced | verification_unavailable
    fixed_tests: list[str] = field(default_factory=list)
    still_failing: list[str] = field(default_factory=list)
    new_failures: list[str] = field(default_factory=list)   # passed before, not passing now (incl. missing/skipped)
    errors: list[str] = field(default_factory=list)

@dataclass
class EditorVerificationResult:
    patch_applied: bool = False
    apply_log: str = ""
    baseline: CommandResult | None = None
    patched: CommandResult | None = None
    complete: bool = False
    safe_to_apply: bool = False
    tests_pass_after: bool = False
    baseline_failures: list[str] = field(default_factory=list)
    still_failing: list[str] = field(default_factory=list)
    new_failures: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    verdict: str = ""

# --------------------------------------------------------------------------- helpers

def _write_extra(workdir: Path, extra_files: dict[str, str] | None) -> None:
    """Drop extra files (e.g. a generated reproduction test) into a throwaway copy."""
    root = workdir.resolve()
    for rel, text in (extra_files or {}).items():
        target = (root / rel).resolve()
        if root not in target.parents:
            raise ValueError(f"extra file escapes the sandbox: {rel!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


def _clean_env(workdir: Path) -> dict[str, str]:
    """Minimal environment: no API keys, tokens or other secrets leak in."""
    keep = ["PATH", "SYSTEMROOT", "TEMP", "TMP", "HOME", "USERPROFILE", "COMSPEC", "PATHEXT"]
    env = {k: os.environ[k] for k in keep if k in os.environ}
    env["PYTHONPATH"] = str(workdir)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env



def _trim(text: str) -> str:
    if len(text) <= MAX_LOG_CHARS:
        return text

    marker = "\n...[truncated]...\n"
    available = MAX_LOG_CHARS - len(marker)
    left = available // 2
    right = available - left

    return text[:left] + marker + text[-right:]


_FAILED_RE = re.compile(r"^(?:FAILED|ERROR)\s+(\S+)", re.M)


def _failed_ids(text: str) -> list[str]:
    out: list[str] = []
    for m in _FAILED_RE.findall(text):
        m = m.replace("\\", "/").removeprefix("./")
        if m not in out:
            out.append(m)
    return out


def _read_junit(path: Path) -> tuple[dict[str, str], bool, bool]:
    """
    Return test outcomes, whether JUnit is valid, and whether collection
    or test execution errors were reported by the JUnit suites.
    """
    out: dict[str, str] = {}

    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return out, False, False

    if root.tag not in {"testsuite", "testsuites"}:
        return out, False, False

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))

    collection_errors = any(
        int(suite.get("errors", "0") or 0) > 0
        for suite in suites
    )

    for tc in root.iter("testcase"):
        classname = tc.get("classname", "")
        name = tc.get("name", "")

        if not name:
            collection_errors = True
            continue

        key = f"{classname}::{name}"

        if tc.find("skipped") is not None:
            out[key] = "skipped"
        elif tc.find("error") is not None:
            out[key] = "error"
            if not classname:
                collection_errors = True
        elif tc.find("failure") is not None:
            out[key] = "failed"
        else:
            out[key] = "passed"

    return out, True, collection_errors


def _parse_junit(path: Path) -> dict[str, str]:
    """Backward-compatible JUnit parser used by existing callers."""
    outcomes, _, _ = _read_junit(path)
    return outcomes


def _module_of(key: str) -> str:
    """Module part of a junit key. 'pkg.test_a.TestX::test_y' -> 'pkg.test_a' is not
    recoverable exactly, so we return the raw classname; use _matches_module() to compare."""
    return key.split("::", 1)[0]


def _norm_collection_name(name: str) -> str:
    return name.replace("\\", "/").removesuffix(".py").replace("/", ".")


def _matches_module(key: str, module: str) -> bool:
    """True if this junit entry belongs to `module` (dotted path, e.g. 'test_miniai_repro')."""
    classname, _, name = key.partition("::")
    if classname:
        return classname == module or classname.startswith(module + ".")
    # collection/import error: no classname, the name is the file/module
    n = _norm_collection_name(name)
    return n == module or n.endswith("." + module) or module.endswith("." + n)


def _repro_modules(extra_files: dict[str, str] | None) -> list[str]:
    mods = []
    for rel in (extra_files or {}):
        if rel.endswith(".py"):
            mods.append(Path(rel.replace("\\", "/")).with_suffix("").as_posix().replace("/", "."))
    return mods


# --------------------------------------------------------------------------- judging

def _judge(result: VerificationResult, repro_modules: list[str] | None = None) -> None:
    """Decide whether the patch fixed the reported bug.

    With a repro test: it must fail before, pass after, and nothing else may break.
    Without one: some previously failing test must now genuinely pass, nothing else may break.
    """
    b, p = result.baseline, result.patched
    if b is None or p is None:
        return

    def unavailable(reason: str) -> None:
        result.fixed = False
        result.verdict = V_UNAVAILABLE
        result.errors.append(reason)

    if b.timed_out or p.timed_out:
        return unavailable("test run timed out")
    if b.exit_code == 0:
        result.fixed = False
        result.verdict = V_NOT_REPRODUCED
        result.errors.append("baseline had no failing tests: the reported bug was not reproduced")
        return
    if b.exit_code != 1:
        return unavailable(f"baseline exit code {b.exit_code} (5 = no tests collected)")
    if p.exit_code not in (0, 1):
        return unavailable(f"patched run exit code {p.exit_code}")
    if not b.outcomes or not p.outcomes:
        return unavailable("no JUnit results (is the test command pytest?)")

    # Regression: anything that passed before must still PASS (missing/skipped counts as broken).
    broken = sorted(k for k, v in b.outcomes.items() if v == "passed" and p.outcomes.get(k) != "passed")
    result.new_failures = broken

    # ---- repro-aware path
    if repro_modules:
        def pick(outcomes: dict[str, str]) -> dict[str, str]:
            return {k: v for k, v in outcomes.items() if any(_matches_module(k, m) for m in repro_modules)}

        before, after = pick(b.outcomes), pick(p.outcomes)
        if not before:
            return unavailable("reproduction test was not collected in the baseline run")
        if any(v == "skipped" for v in before.values()):
            return unavailable("reproduction test was skipped in the baseline run")
        if all(v == "passed" for v in before.values()):
            result.fixed = False
            result.verdict = V_NOT_REPRODUCED
            result.errors.append("reproduction test already passes before the patch")
            return
        if not all(v in BAD for v in before.values()):
            return unavailable("mixed reproduction outcomes in the baseline run")

        import_failure = any(not k.split("::", 1)[0] for k in before)
        if import_failure:
            return unavailable(
                "reproduction test failed during collection; "
                "the same test cannot be verified after the patch"
            )
        same_tests = set(before) <= set(after)

        all_pass = bool(after) and all(v == "passed" for v in after.values())

        # Include reproduction tests that still fail and pre-existing failures
        # that remain after the patch. Do not include newly introduced failures
        # here; those are tracked separately in new_failures.
        remaining = {
            k for k, v in after.items()
            if v in BAD
        }

        remaining.update(
            k for k, v in b.outcomes.items()
            if v in BAD and p.outcomes.get(k) in BAD
        )

        result.still_failing = sorted(remaining)
        if not same_tests:
            result.fixed = False
            result.verdict = V_NOT_FIXED
            result.errors.append("reproduction test is missing after the patch (deselected, deleted or renamed)")
            return
        if any(v == "skipped" for v in after.values()):
            result.fixed = False
            result.verdict = V_NOT_FIXED
            result.errors.append("reproduction test was skipped after the patch")
            return
        if not all_pass:
            result.fixed = False
            result.verdict = V_NOT_FIXED
            return
        result.fixed_tests = sorted(after)
        if broken:
            result.fixed = False
            result.verdict = V_REGRESSION
            return
        result.fixed = True
        result.verdict = V_FIXED
        return

    # ---- no repro test: fall back to "some failing test now truly passes"
    bad_before = {k for k, v in b.outcomes.items() if v in BAD}
    if not bad_before:
        return unavailable("could not identify failing tests in the baseline run")

    fixed: set[str] = set()
    still: set[str] = set()
    for k in bad_before:
        classname, _, name = k.partition("::")
        if classname:
            status = p.outcomes.get(k)
            if status == "passed":
                fixed.add(k)
            else:
                still.add(k)             # failed, error, skipped or missing: not proven fixed
        else:
            # file failed to import before; fixed only if tests inside it now run and all pass
            module = _norm_collection_name(name)
            inside = {kk: vv for kk, vv in p.outcomes.items() if _matches_module(kk, module)}
            if inside and all(v == "passed" for v in inside.values()):
                fixed.add(k)
            else:
                still.add(k)

    result.fixed_tests = sorted(fixed)
    result.still_failing = sorted(still)
    if broken:
        result.fixed = False
        result.verdict = V_REGRESSION
    elif fixed:
        result.fixed = True
        result.verdict = V_FIXED
    else:
        result.fixed = False
        result.verdict = V_NOT_FIXED


# --------------------------------------------------------------------------- running

def _run(cmd: list[str], cwd: Path, timeout: int) -> CommandResult:
    start = time.monotonic()
    # On Windows, put the child in its own process group so a console
    # Ctrl+C aimed at the server doesn't kill the test run too.
    extra = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {}
    try:
        p = subprocess.run(
            cmd, cwd=cwd, env=_clean_env(cwd), capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout,
            **extra,
        )
        out = p.stdout + p.stderr
        return CommandResult(cmd, p.returncode, _trim(out), time.monotonic() - start, failed_tests=_failed_ids(out))
    except subprocess.TimeoutExpired as e:
        out = (e.stdout or "") if isinstance(e.stdout, str) else ""
        return CommandResult(cmd, None, _trim(out + "\n[TIMED OUT]"), time.monotonic() - start, timed_out=True)
    except OSError as e:
        return CommandResult(cmd, None, f"Failed to start: {e}", time.monotonic() - start)


# def _run_tests(cmd: list[str], cwd: Path, timeout: int) -> CommandResult:
#     """Run pytest with JUnit XML output written OUTSIDE the repo copy."""
#     xml_path = cwd.parent / f"{cwd.name}-junit.xml"
#     full = list(cmd)
#     if any("pytest" in part for part in cmd):
#         full += [f"--junitxml={xml_path}", "-p", "no:cacheprovider"]
#     r = _run(full, cwd, timeout)
#     # pytest exits with 0-5; anything else is a crash/interrupt, not a test result
#     if r.exit_code is not None and not (0 <= r.exit_code <= 5):
#         r = _run(full, cwd, timeout)
#     r.outcomes = _parse_junit(xml_path)
#     return r


def _run_tests(cmd: list[str], cwd: Path, timeout: int) -> CommandResult:
    """Run pytest directly in the GitHub Actions runner."""
    xml_path = cwd.parent / f"{cwd.name}-junit.xml"

    full = list(cmd)

    if any("pytest" in part for part in cmd):
        full += [
            f"--junitxml={xml_path}",
            "-p",
            "no:cacheprovider",
        ]

    result = _run(full, cwd, timeout)

    if any("pytest" in part for part in cmd):
        (
            result.outcomes,
            result.junit_valid,
            result.collection_errors,
        ) = _read_junit(xml_path)

    return result



# --------------------------------------------------------------------------- patch guard


def validate_diff_paths(diff: str) -> list[str]:
    """Reject diffs that touch absolute paths or escape the repo root."""
    problems = []

    for line in diff.splitlines():
        if not line.startswith(("--- ", "+++ ")):
            continue

        path = line[4:].split("\t")[0].strip()

        if path == "/dev/null":
            continue

        path = re.sub(r"^[ab]/", "", path)
        normalized = path.replace("\\", "/")

        parts = normalized.split("/")

        if (
            normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or ".." in parts
        ):
            problems.append(f"Unsafe path in diff: {path}")

    return problems


def _is_test_path(path: str) -> bool:
    p = Path(path)
    return p.name.startswith("test_") or p.name.endswith("_test.py") or "tests" in p.parts


_SKIP_RE = re.compile(r"^\+.*(pytest\.mark\.(skip|skipif|xfail)|pytest\.skip\(|pytest\.xfail\(|unittest\.skip)")


def guard_diff(diff: str, extra_files: dict[str, str] | None = None) -> list[str]:
    """Reject patches that could fake a pass: config edits, touching the repro test,
    deleting/renaming tests, or adding skip/xfail markers."""
    problems: list[str] = []
    extras = {k.replace("\\", "/") for k in (extra_files or {})}
    removed_tests = added_tests = 0
    old_path = new_path = ""

    for line in diff.splitlines():
        if line.startswith("--- "):
            old_path = re.sub(r"^[ab]/", "", line[4:].split("\t")[0].strip())
        elif line.startswith("+++ "):
            new_path = re.sub(r"^[ab]/", "", line[4:].split("\t")[0].strip())
            for path in {old_path, new_path} - {"/dev/null", ""}:
                path = path.replace("\\", "/")
                if Path(path).name in PROTECTED_FILES:
                    problems.append(f"patch touches protected file: {path}")
                if path in extras:
                    problems.append(f"patch touches the reproduction test: {path}")
            if new_path == "/dev/null" and old_path and _is_test_path(old_path):
                problems.append(f"patch deletes a test file: {old_path}")
        elif line.startswith(("rename from ", "rename to ")):
            path = line.split(" ", 2)[2].strip()
            if _is_test_path(path) or Path(path).name in PROTECTED_FILES:
                problems.append(f"patch renames a test/config file: {path}")
        elif re.match(r"^-\s*(async\s+)?def test_", line):
            removed_tests += 1
        elif re.match(r"^\+\s*(async\s+)?def test_", line):
            added_tests += 1
        elif _SKIP_RE.match(line):
            problems.append(f"patch adds a skip/xfail marker: {line[1:].strip()[:80]}")

    if removed_tests > added_tests:
        problems.append("patch removes or renames existing tests")
    return problems


def apply_diff(workdir: Path, diff: str, extra_files: dict[str, str] | None = None) -> tuple[bool, str]:
    problems = validate_diff_paths(diff) + guard_diff(diff, extra_files)
    if problems:
        return False, "; ".join(dict.fromkeys(problems))
    patch_file = workdir / "_candidate.patch"
    patch_file.write_text(diff if diff.endswith("\n") else diff + "\n", encoding="utf-8", newline="\n")
    r = _run(["git", "apply", "--whitespace=nowarn", str(patch_file)], workdir, timeout=30)
    patch_file.unlink(missing_ok=True)
    return r.exit_code == 0, r.output

# --------------------------------------------------------------------------- editor verification

def _editor_run_complete(run: CommandResult | None) -> bool:
    """Accept only completed pytest runs with valid, non-empty JUnit results."""
    if run is None:
        return False

    if run.timed_out or run.exit_code not in (0, 1):
        return False

    if not run.junit_valid or not run.outcomes or run.collection_errors:
        return False

    has_failures = any(status in BAD for status in run.outcomes.values())

    # The pytest exit code and JUnit results must agree.
    if run.exit_code == 0 and has_failures:
        return False

    if run.exit_code == 1 and not has_failures:
        return False

    return True


def verify_editor_patch(
    repo_path: str,
    diff: str,
    test_cmd: list[str] | None = None,
    timeout: int = 120,
) -> EditorVerificationResult:
    """
    Verify arbitrary editor changes against fresh baseline and patched runs.

    This is separate from _judge(): it does not claim that a reported bug
    was fixed. It determines whether the candidate introduces regressions.
    """
    test_cmd = test_cmd or DEFAULT_TEST_CMD
    result = EditorVerificationResult()
    src = Path(repo_path)

    if not src.is_dir():
        result.verdict = V_UNAVAILABLE
        result.errors.append(f"Repo path not found: {repo_path}")
        return result

    tmp = Path(tempfile.mkdtemp(prefix="editor-verify-"))

    try:
        # Run the baseline independently.
        base_dir = tmp / "baseline"
        shutil.copytree(src, base_dir, ignore=IGNORE)
        result.baseline = _run_tests(test_cmd, base_dir, timeout)

        # Prepare an independent patched copy.
        patched_dir = tmp / "patched"
        shutil.copytree(src, patched_dir, ignore=IGNORE)

        ok, log = apply_diff(patched_dir, diff)
        result.patch_applied = ok
        result.apply_log = log

        if not ok:
            result.verdict = V_UNAVAILABLE
            result.errors.append(f"Patch rejected or failed to apply: {log[:500]}")
            return result

        result.patched = _run_tests(test_cmd, patched_dir, timeout)

        # Both runs must be complete before making a safety decision.
        if not _editor_run_complete(result.baseline):
            result.verdict = V_UNAVAILABLE
            result.errors.append("Baseline test run was incomplete or invalid.")
            return result

        if not _editor_run_complete(result.patched):
            result.verdict = V_UNAVAILABLE
            result.errors.append("Patched test run was incomplete or invalid.")
            return result

        baseline = result.baseline.outcomes
        patched = result.patched.outcomes

        result.baseline_failures = sorted(
            key for key, status in baseline.items() if status in BAD
        )

        # A previously passing test must remain passing.
        regressions = {
            key
            for key, status in baseline.items()
            if status == "passed" and patched.get(key) != "passed"
        }

        # A pre-existing failure must not disappear through skipping or
        # deselection. It may remain failing or become passing.
        regressions.update(
            key
            for key, status in baseline.items()
            if status in BAD and patched.get(key) in (None, "skipped")
        )

        # Newly failing tests, including tests absent from the baseline.
        regressions.update(
            key
            for key, status in patched.items()
            if status in BAD and baseline.get(key) not in BAD
        )

        result.new_failures = sorted(regressions)

        result.still_failing = sorted(
            key
            for key, status in baseline.items()
            if status in BAD and patched.get(key) in BAD
        )

        result.complete = True
        result.tests_pass_after = (
            result.patched.exit_code == 0
            and not any(status in BAD for status in patched.values())
        )

        result.safe_to_apply = not result.new_failures

        if result.safe_to_apply:
            result.verdict = "safe_to_apply"
        else:
            result.verdict = V_REGRESSION
            result.errors.append(
                "The patch caused test regressions or hid existing failures."
            )

    except (OSError, shutil.Error, ValueError) as exc:
        result.verdict = V_UNAVAILABLE
        result.errors.append(f"Editor verification failed: {exc}")

    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return result

# --------------------------------------------------------------------------- public API

def run_baseline(
    repo_path: str,
    test_cmd: list[str] | None = None,
    timeout: int = 120,
    extra_files: dict[str, str] | None = None,
) -> CommandResult:
    test_cmd = test_cmd or DEFAULT_TEST_CMD
    src = Path(repo_path)
    if not src.is_dir():
        return CommandResult(test_cmd, None, f"Repo path not found: {repo_path}", 0.0)
    tmp = Path(tempfile.mkdtemp(prefix="ai-sandbox-"))
    try:
        base_dir = tmp / "baseline"
        shutil.copytree(src, base_dir, ignore=IGNORE)
        _write_extra(base_dir, extra_files)
        return _run_tests(test_cmd, base_dir, timeout)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def verify_patch(
    repo_path: str,
    diff: str,
    test_cmd: list[str] | None = None,
    timeout: int = 120,
    baseline: CommandResult | None = None,
    extra_files: dict[str, str] | None = None,
) -> VerificationResult:
    """Run tests before and after the patch, each in a throwaway copy.

    Pass `baseline` to reuse an earlier baseline run (it must have been produced
    with the same `extra_files`, otherwise the repro test will look 'not collected').
    `extra_files` are written into both copies; their .py files are treated as the
    reproduction test(s) that must go from failing to passing."""
    test_cmd = test_cmd or DEFAULT_TEST_CMD
    result = VerificationResult(patch_applied=False)
    src = Path(repo_path)
    if not src.is_dir():
        result.verdict = V_UNAVAILABLE
        result.errors.append(f"Repo path not found: {repo_path}")
        return result

    tmp = Path(tempfile.mkdtemp(prefix="ai-sandbox-"))
    try:
        if baseline is None:
            base_dir = tmp / "baseline"
            shutil.copytree(src, base_dir, ignore=IGNORE)
            _write_extra(base_dir, extra_files)
            baseline = _run_tests(test_cmd, base_dir, timeout)
        result.baseline = baseline

        patched_dir = tmp / "patched"
        shutil.copytree(src, patched_dir, ignore=IGNORE)
        _write_extra(patched_dir, extra_files)
        ok, log = apply_diff(patched_dir, diff, extra_files)
        result.patch_applied, result.apply_log = ok, log
        if ok:
            result.patched = _run_tests(test_cmd, patched_dir, timeout)
            _judge(result, _repro_modules(extra_files))
        else:
            result.verdict = V_NOT_FIXED
            result.errors.append(f"patch rejected or failed to apply: {log[:300]}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return result