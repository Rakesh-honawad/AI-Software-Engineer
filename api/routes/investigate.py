
"""
api/routes/investigate.py

POST /repositories/{id}/investigate — the bug-to-verified-patch workflow:
baseline tests -> (if no failing test is about the report) reproduce the bug with a generated test
-> localize (graph + evidence) -> propose edits -> build diff -> verify in a
throwaway copy -> one retry using the new failure output.

Repository access is restricted to the authenticated Supabase user.
"""

from __future__ import annotations

import ast
import asyncio
import os
import re
from dataclasses import asdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ai.reasoning.bug_localizer import (
    is_test_path,
    localize,
    strip_selected_suffix,
)
from ai.reasoning.patch_proposer import (
    _parse,
    build_diff,
    patched_contents,
    propose_patch,
)
from ai.reasoning.repro_test import (
    REPRO_FILE,
    classify_repro_run,
    generate_repro,
    repro_touches_suspects,
)
from ai.router.orchestrator import run_known_task
from ai.router.router_v2 import AllFamiliesFailedError
from api.dependencies.repository_deps import (
    get_storage_dir_or_404,
    load_graph_for_repo,
)
from auth.dependencies import AuthenticatedUser, get_current_user
from repository.manager.repository_manager import load_record
from sandbox.runner import DEFAULT_TEST_CMD, run_baseline, verify_patch


router = APIRouter(
    prefix="/repositories",
    tags=["investigate"],
)

MAX_ATTEMPTS = 2
TOP_K = 6
LLM_TRIES = 2
LLM_TEMPERATURE: float | None = None
LLM_MAX_TOKENS = 4000
REPRO_RUNS = 3

_SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
}


class InvestigateRequest(BaseModel):
    bug_report: str
    test_path: str | None = None
    focus_node_ids: list[str] | None = None
    allow_unverified_patch: bool = False


def _cmd(c) -> dict | None:
    """CommandResult as a dict for the API."""
    if c is None:
        return None

    data = asdict(c)
    data.pop("outcomes", None)
    return data


_IDENT_CALL_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*(?=\s*\()"
)

_IDENT_ANY_RE = re.compile(
    r"[A-Za-z_][A-Za-z0-9_]*"
)


def report_names_symbol(bug_report: str, nodes) -> bool:
    """
    True when the report explicitly names a code symbol that exists
    in the graph.

    A multi-part identifier counts anywhere. A plain one-word name
    counts only when written as a call.
    """

    text = strip_selected_suffix(bug_report)

    names = {
        node.name
        for node in nodes
        if node.name
        and node.file_path
        and not is_test_path(node.file_path)
        and not getattr(node, "is_deleted", False)
    }

    called = set(_IDENT_CALL_RE.findall(text))

    for ident in set(_IDENT_ANY_RE.findall(text)):
        if ident not in names:
            continue

        multipart = (
            "_" in ident.strip("_")
            or any(char.isupper() for char in ident[1:])
        )

        if multipart or ident in called:
            return True

    return False


_CONFIG_FILES = {
    "pytest.ini",
    "setup.cfg",
    "tox.ini",
    "pyproject.toml",
    "conftest.py",
    ".coveragerc",
}


def edits_touch_tests(edits: list[dict]) -> list[str]:
    """
    A patch must fix the code, never edit tests or pytest configuration
    to make a failure disappear.
    """

    bad = []

    for edit in edits:
        file_path = (
            str(edit.get("file", ""))
            .replace("\\", "/")
            .removeprefix("./")
        )

        if file_path and (
            is_test_path(file_path)
            or file_path.split("/")[-1] in _CONFIG_FILES
        ):
            bad.append(file_path)

    return bad


def find_syntax_errors(
    repo_path: str,
    limit: int = 2000,
) -> list[dict]:
    """
    Parse every Python file.

    A file that cannot be parsed is a syntax error, not a
    business-logic bug.
    """

    output: list[dict] = []
    root = Path(repo_path)
    scanned = 0

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            directory
            for directory in dirnames
            if directory not in _SKIP_DIRS
        ]

        for filename in filenames:
            if not filename.endswith(".py"):
                continue

            scanned += 1

            if scanned > limit:
                return output

            path = Path(dirpath, filename)

            try:
                ast.parse(
                    path.read_text(
                        encoding="utf-8",
                        errors="replace",
                    ),
                    filename=str(path),
                )

            except SyntaxError as exc:
                output.append(
                    {
                        "file": path.relative_to(root).as_posix(),
                        "line": exc.lineno,
                        "message": exc.msg,
                        "text": (exc.text or "").rstrip(),
                    }
                )

            except (
                OSError,
                ValueError,
                RecursionError,
                MemoryError,
            ):
                continue

    return output


def _canon(test_id: str) -> str:
    """
    Normalize test IDs.

    Console:
      tests/test_cart.py::TestCart::test_x

    JUnit:
      tests.test_cart.TestCart::test_x

    Both become:
      tests.test_cart.TestCart.test_x
    """

    test_id = (
        test_id
        .replace("\\", "/")
        .removeprefix("./")
    )

    if "::" not in test_id:
        return test_id.removesuffix(".py").replace("/", ".")

    head, _, rest = test_id.partition("::")

    if head.endswith(".py"):
        head = head[:-3].replace("/", ".")

    return (
        (head + ".") if head else ""
    ) + rest.replace("::", ".")


def evidence_was_fixed(
    verification,
    reproduced: bool,
    has_related_failure: bool,
    related_tests: list[str],
) -> bool:
    """
    The test providing evidence for the report must itself go from
    failing to passing.
    """

    if reproduced:
        return verification.verdict == "fixed"

    if has_related_failure:
        if not verification.fixed:
            return False

        fixed = {
            _canon(test)
            for test in verification.fixed_tests
        }

        for related in (
            _canon(test)
            for test in related_tests
        ):
            if related in fixed or any(
                item.endswith("." + related)
                for item in fixed
            ):
                return True

        return False

    return True


_BLOCK_HEAD_RE = re.compile(
    r"^_{3,} (.+?) _{3,}\s*$",
    re.M,
)

_FAILED_ID_RE = re.compile(
    r"^(?:FAILED|ERROR)\s+(\S+)",
    re.M,
)


def related_failures(
    output: str,
    suspects,
    top_n: int = 3,
) -> tuple[str, list[str], list[str]]:
    """
    Keep only pytest failures that relate to the reported code.

    Returns:
      related_text,
      related_test_ids,
      unrelated_test_ids
    """

    output = output or ""

    heads = list(_BLOCK_HEAD_RE.finditer(output))

    if not heads:
        return "", [], []

    ids = _FAILED_ID_RE.findall(output)

    names = [
        suspect.node.name
        for suspect in suspects[:top_n]
        if suspect.node.name
    ]

    paths = [
        suspect.node.file_path
        .replace("\\", "/")
        .removeprefix("./")
        for suspect in suspects[:top_n]
        if suspect.node.file_path
    ]

    name_re = (
        re.compile(
            r"\b(?:"
            + "|".join(re.escape(name) for name in names)
            + r")\b"
        )
        if names
        else None
    )

    path_res = [
        re.compile(
            r"""(?:^|[/"])"""
            + re.escape(path)
            + r"""(?::\d+|"\s*,\s*line\s+\d+)"""
        )
        for path in paths
    ]

    related_text: list[str] = []
    related_ids: list[str] = []
    unrelated_ids: list[str] = []

    for index, match in enumerate(heads):
        end = (
            heads[index + 1].start()
            if index + 1 < len(heads)
            else len(output)
        )

        section = re.split(
            r"^={3,}",
            output[match.start():end],
            maxsplit=1,
            flags=re.M,
        )[0].rstrip()

        name = match.group(1)

        test_id = next(
            (
                item
                for item in ids
                if item.endswith("::" + name) or item == name
            ),
            name,
        )

        normalized_text = section.replace("\\", "/")

        if (
            name_re and name_re.search(section)
        ) or any(
            pattern.search(normalized_text)
            for pattern in path_res
        ):
            related_text.append(section)
            related_ids.append(test_id)
        else:
            unrelated_ids.append(test_id)

    return (
        "\n\n".join(related_text),
        related_ids,
        unrelated_ids,
    )


@router.post("/{repository_id}/investigate")
async def investigate(
    repository_id: str,
    request: InvestigateRequest,
    user: AuthenticatedUser = Depends(get_current_user),
) -> dict:
    """
    Investigate a reported bug and propose a verified patch.

    Requires a valid Supabase user and ownership of the repository.
    """

    # Resolve the repository using the authenticated owner.
    storage_dir = get_storage_dir_or_404(
        repository_id,
        user.id,
    )

    record = load_record(storage_dir)

    if record is None:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        )

    # The repository record must belong to this authenticated user.
    if record.owner_id != user.id:
        raise HTTPException(
            status_code=404,
            detail="Repository not found.",
        )

    repo_path = record.local_path

    if request.test_path:
        test_path = request.test_path.replace("\\", "/")

        if (
            test_path.startswith("-")
            or ".." in test_path.split("/")
            or test_path.startswith("/")
            or ":" in test_path
        ):
            raise HTTPException(
                status_code=400,
                detail=(
                    "test_path must be a relative path "
                    "inside the repository."
                ),
            )

    tp = request.test_path

    test_cmd = (
        [*DEFAULT_TEST_CMD, tp]
        if tp
        else DEFAULT_TEST_CMD
    )

    # Load graph only after verifying ownership.
    nodes, edges = load_graph_for_repo(
        repository_id,
        user.id,
    )

    models_used: list[str] = []

    async def llm_call(prompt: str) -> str:
        """
        Ask the model for JSON.

        If the answer is not parseable JSON, ask a different model
        once when possible.
        """

        avoid: set[str] = set()
        text = ""

        for attempt in range(LLM_TRIES):
            try:
                result = await run_known_task(
                    task_type="patch_generate",
                    service_tier="startup_top",
                    system=(
                        "You are an expert software engineer fixing "
                        "a bug. Respond with JSON only."
                    ),
                    prompt=prompt,
                    max_tokens=LLM_MAX_TOKENS,
                    avoid_models=avoid,
                    temperature=LLM_TEMPERATURE,
                )

            except AllFamiliesFailedError:
                if attempt == 0:
                    raise

                return text

            model_id = str(result.get("model_id") or "")

            if model_id:
                models_used.append(model_id)

            text = result["final_text"]

            try:
                _parse(text)
                return text

            except Exception:
                if model_id:
                    avoid.add(model_id)

        return text

    def run_localize(failure_text: str):
        return localize(
            request.bug_report,
            failure_text,
            nodes,
            edges,
            top_k=TOP_K,
            focus_node_ids=request.focus_node_ids,
            repo_root=repo_path,
        )

    # 0. Parse Python files for syntax errors.
    syntax_errors = await asyncio.to_thread(
        find_syntax_errors,
        repo_path,
    )

    # 1. Run baseline tests on an untouched copy.
    baseline = await asyncio.to_thread(
        run_baseline,
        repo_path,
        test_cmd,
    )

    if baseline.exit_code is None:
        return {
            "status": "tests_could_not_run",
            "suspects": [],
            "baseline": _cmd(baseline),
            "attempts": [],
            "message": (
                "The test command timed out or could not start. "
                "Fix the environment and retry."
            ),
        }

    baseline_failed = baseline.exit_code not in (0, 5)

    failure_output = ""
    verification_baseline = baseline

    # 2. Initial localization.
    suspects = run_localize(failure_output)

    # 2b. Syntax errors in reported code take priority.
    report_text = request.bug_report.replace("\\", "/")

    suspect_files = {
        suspect.node.file_path
        .replace("\\", "/")
        .removeprefix("./")
        for suspect in suspects
        if suspect.node.file_path
    }

    blocking = [
        error
        for error in syntax_errors
        if (
            error["file"] in suspect_files
            or error["file"] in report_text
            or error["file"].split("/")[-1] in report_text
        )
    ]

    if blocking:
        return {
            "status": "syntax_error",
            "suspects": [
                suspect.to_dict()
                for suspect in suspects
            ],
            "baseline": _cmd(baseline),
            "attempts": [],
            "syntax_errors": blocking,
            "message": (
                "A file in the reported code cannot be parsed. "
                "This is a syntax error, not a business-logic bug; "
                "fix it first, then re-run the investigation."
            ),
        }

    if not suspects:
        return {
            "status": "no_suspects",
            "suspects": [],
            "baseline": _cmd(baseline),
            "attempts": [],
            "syntax_errors": syntax_errors,
            "message": (
                "No symbols matched the bug report or test output."
            ),
        }

    # 3. Check whether an existing failing test relates to the report.
    original_test_cmd = test_cmd

    (
        related_text,
        related_tests,
        unrelated_tests,
    ) = (
        related_failures(baseline.output, suspects)
        if baseline_failed
        else ("", [], [])
    )

    has_related_failure = bool(related_text)

    if has_related_failure:
        suspects = run_localize(related_text) or suspects

    failure_output = related_text

    reproduction: dict = {
        "status": "skipped",
        "note": (
            "An existing failing test is about the reported code, "
            "so it is used as the evidence."
        ),
    }

    extra_files: dict[str, str] | None = None
    reproduced = False

    if not has_related_failure:
        reproduction = {
            "status": "not_reproduced",
            "note": "",
        }

        try:
            repro = await generate_repro(
                request.bug_report,
                suspects,
                repo_path,
                llm_call,
            )

        except AllFamiliesFailedError as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"No AI model is currently available: {exc}"
                ),
            ) from exc

        if repro.not_reproducible:
            reproduction = {
                "status": "not_reproducible",
                "note": repro.reason,
            }

        elif repro.error:
            reproduction = {
                "status": "invalid_test",
                "note": (
                    "The generated reproduction test was rejected: "
                    + repro.error
                ),
            }

        else:
            # Run the generated test alone. Unrelated failures must
            # neither hide nor fake reproduction.
            repro_cmd = [
                *DEFAULT_TEST_CMD,
                REPRO_FILE,
            ]

            repro_run = await asyncio.to_thread(
                run_baseline,
                repo_path,
                repro_cmd,
                120,
                {REPRO_FILE: repro.code},
            )

            ok, why = classify_repro_run(repro_run)
            unstable = False

            if ok and not repro_touches_suspects(
                repro_run.output,
                suspects,
            ):
                ok = False
                why = (
                    "The test failed inside code that is not one "
                    "of the reported suspects."
                )

            if ok:
                for _ in range(REPRO_RUNS - 1):
                    again = await asyncio.to_thread(
                        run_baseline,
                        repo_path,
                        repro_cmd,
                        120,
                        {REPRO_FILE: repro.code},
                    )

                    ok_again, _why = classify_repro_run(again)

                    if not ok_again:
                        ok = False
                        unstable = True
                        why = (
                            f"The reproduction test is unstable: "
                            f"it failed once but not on all "
                            f"{REPRO_RUNS} runs, so it cannot prove "
                            "or disprove a fix."
                        )
                        break

            reproduction["note"] = why

            if not ok:
                reproduction["status"] = (
                    "unstable"
                    if unstable
                    else "invalid_test"
                )

            else:
                reproduction = {
                    "status": "reproduced",
                    "note": why,
                    "file": REPRO_FILE,
                    "code": repro.code,
                    "expectation": repro.expectation,
                    "quote": repro.quote,
                }

                reproduced = True
                extra_files = {
                    REPRO_FILE: repro.code,
                }

                failure_output = repro_run.output

                # Verification compares against the full suite plus
                # the generated reproduction test.
                test_cmd = (
                    [*test_cmd, REPRO_FILE]
                    if tp
                    else test_cmd
                )

                verification_baseline = await asyncio.to_thread(
                    run_baseline,
                    repo_path,
                    test_cmd,
                    120,
                    extra_files,
                )

                def _repro_in_run(result) -> bool:
                    return any(
                        test.split("::")[0] == REPRO_FILE
                        for test in result.failed_tests
                    )

                if (
                    not _repro_in_run(verification_baseline)
                    and REPRO_FILE not in test_cmd
                ):
                    # pytest.ini testpaths may hide a root-level file.
                    test_cmd = [
                        *test_cmd,
                        ".",
                        REPRO_FILE,
                    ]

                    verification_baseline = await asyncio.to_thread(
                        run_baseline,
                        repo_path,
                        test_cmd,
                        120,
                        extra_files,
                    )

                if not _repro_in_run(verification_baseline):
                    reproduced = False

                    if verification_baseline.exit_code is None:
                        reproduction = {
                            "status": "verification_unavailable",
                            "note": (
                                "The full verification run timed out "
                                "or could not start, so the generated "
                                "test cannot be used to judge a patch."
                            ),
                        }

                    else:
                        reproduction = {
                            "status": "invalid_test",
                            "note": (
                                "The generated test could not be "
                                "included in the verification run."
                            ),
                        }

                    extra_files = None
                    test_cmd = original_test_cmd
                    verification_baseline = baseline
                    failure_output = related_text

                suspects = (
                    run_localize(failure_output)
                    or suspects
                )

    has_evidence = has_related_failure or reproduced

    # 4. Do not guess a patch without evidence unless explicitly opted in.
    if (
        not has_evidence
        and not request.allow_unverified_patch
    ):
        return {
            "status": "no_patch",
            "suspects": [
                suspect.to_dict()
                for suspect in suspects
            ],
            "baseline": _cmd(baseline),
            "unrelated_failures": unrelated_tests,
            "syntax_errors": syntax_errors,
            "reproduction": reproduction,
            "root_cause": "",
            "explanation": "",
            "confidence": "low",
            "diff": "",
            "files": [],
            "attempts": [],
            "message": (
                "No existing failing test is about the reported code "
                "and the reported behaviour could not be reproduced, "
                "so no patch was generated. "
                + (reproduction.get("note") or "")
                + " Re-run with allow_unverified_patch=true "
                "to ask for an unverified patch anyway."
            ),
        }

    attempts: list[dict] = []
    grounded = ""

    if reproduced:
        grounded = (
            f'Quote: "{reproduction.get("quote", "")}"\n'
            "Expected behaviour: "
            f'{reproduction.get("expectation", "")}'
        )

    try:
        for number in range(1, MAX_ATTEMPTS + 1):
            proposal = await propose_patch(
                request.bug_report,
                failure_output,
                suspects,
                repo_path,
                llm_call,
                attempts,
                grounded_expectation=grounded,
            )

            entry = {
                "attempt": number,
                "root_cause": proposal.root_cause,
                "explanation": proposal.explanation,
                "confidence": proposal.confidence,
                "diff": "",
                "files": [],
                "error": None,
                "verification": None,
                "verdict": proposal.verdict,
                "verdict_reason": proposal.verdict_reason,
            }

            if (
                not proposal.error
                and proposal.verdict != "code_bug"
            ):
                attempts.append(entry)
                break

            protected = (
                []
                if proposal.error
                else edits_touch_tests(proposal.edits)
            )

            if protected:
                diff, errors = "", [
                    "The patch edits tests or test configuration "
                    f"({', '.join(protected)}), which is not allowed."
                ]

            elif proposal.error:
                diff, errors = "", [proposal.error]

            else:
                diff, errors = build_diff(
                    repo_path,
                    proposal.edits,
                    proposal.allowed_files,
                )

                if not errors and not diff:
                    errors = ["The model proposed no change."]

            if errors:
                entry["error"] = "; ".join(errors)
                attempts.append(entry)
                continue

            entry["files"] = patched_contents(
                repo_path,
                proposal.edits,
                proposal.allowed_files,
            )

            verification = await asyncio.to_thread(
                verify_patch,
                repo_path,
                diff,
                test_cmd,
                120,
                verification_baseline,
                extra_files,
            )

            attempt_fixed = bool(
                verification.fixed
                and evidence_was_fixed(
                    verification,
                    reproduced,
                    has_related_failure,
                    related_tests,
                )
            )

            entry["diff"] = diff

            entry["verification"] = {
                "patch_applied": verification.patch_applied,
                "apply_log": verification.apply_log,
                "patched": _cmd(verification.patched),
                "fixed": attempt_fixed,
                "suite_improved": verification.fixed,
                "verdict": verification.verdict,
                "errors": verification.errors,
                "fixed_tests": verification.fixed_tests,
                "still_failing": verification.still_failing,
                "new_failures": verification.new_failures,
            }

            attempts.append(entry)

            if attempt_fixed:
                break

    except AllFamiliesFailedError as exc:
        raise HTTPException(
            status_code=503,
            detail=f"No AI model is currently available: {exc}",
        ) from exc

    fixed = [
        attempt
        for attempt in attempts
        if (
            attempt["verification"]
            and attempt["verification"]["fixed"]
        )
    ]

    with_diff = [
        attempt
        for attempt in attempts
        if attempt["diff"]
    ]

    best = (
        fixed
        or with_diff
        or attempts
    )[-1]

    best_verdict = (
        best.get("verification") or {}
    ).get("verdict", "")

    review = next(
        (
            attempt
            for attempt in attempts
            if attempt.get("verdict")
            in ("test_or_report_wrong", "unclear")
        ),
        None,
    )

    if fixed and not has_evidence:
        status = "unverified"

    elif fixed:
        status = (
            "fixed_partial"
            if (best["verification"] or {}).get("still_failing")
            else "fixed"
        )

    elif review and not with_diff:
        status = "needs_review"

    elif not with_diff:
        status = "no_patch"

    elif best_verdict == "verification_unavailable":
        status = "verification_unavailable"

    elif not has_evidence:
        status = "unverified"

    else:
        status = "not_fixed"

    if reproduced:
        evidence = "generated_reproduction"

    elif has_related_failure:
        evidence = "existing_failing_test"

    else:
        evidence = "none"

    failing_tests = (
        related_tests[:5]
        if evidence == "existing_failing_test"
        else []
    )

    if reproduced:
        reproduction = {
            **reproduction,
            "verification_passed": bool(
                (best.get("verification") or {}).get(
                    "fixed_tests"
                )
            ),
        }

    return {
        "status": status,
        "suspects": [
            suspect.to_dict()
            for suspect in suspects
        ],
        "baseline": _cmd(baseline),
        "verification_baseline": _cmd(verification_baseline),
        "unrelated_failures": unrelated_tests,
        "syntax_errors": syntax_errors,
        "reproduction": reproduction,
        "root_cause": best["root_cause"],
        "explanation": best["explanation"],
        "confidence": best["confidence"],
        "diff": best["diff"],
        "files": best.get("files", []),
        "attempts": attempts,
        "evidence": evidence,
        "failing_tests": failing_tests,
        "fixed_tests": (
            best.get("verification") or {}
        ).get("fixed_tests", []),
        "remaining_failures": (
            best.get("verification") or {}
        ).get("still_failing", []),
        "verification_verdict": best_verdict,
        "verdict": best.get("verdict", "code_bug"),
        "verdict_reason": best.get("verdict_reason", ""),
        "models_used": sorted(set(models_used)),
    }
