import textwrap
from sandbox.runner import verify_patch

DIFF = textwrap.dedent("""\
    --- a/calc.py
    +++ b/calc.py
    @@ -1,2 +1,2 @@
     def add(a, b):
    -    return a - b
    +    return a + b
    """)


def test_fix_is_verified(tmp_path):
    (tmp_path / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
    )
    r = verify_patch(str(tmp_path), DIFF)
    assert r.patch_applied and r.fixed


from sandbox.runner import (
    V_FIXED,
    V_NOT_FIXED,
    V_NOT_REPRODUCED,
    V_REGRESSION,
    V_UNAVAILABLE,
    guard_diff,
    validate_diff_paths,
)


def test_generated_reproduction_must_fail_before_and_pass_after(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )

    repro = {
        "test_repro.py": (
            "from calc import add\n"
            "def test_reported_bug():\n"
            "    assert add(2, 3) == 5\n"
        )
    }

    result = verify_patch(str(tmp_path), DIFF, extra_files=repro)

    assert result.patch_applied
    assert result.verdict == V_FIXED
    assert result.fixed
    assert any("test_reported_bug" in name for name in result.fixed_tests)


def test_reproduction_already_passing_before_patch_is_not_reproduced(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )

    repro = {
        "test_repro.py": (
            "from calc import add\n"
            "def test_reported_bug():\n"
            "    assert add(2, 3) == -1\n"
        )
    }

    result = verify_patch(str(tmp_path), DIFF, extra_files=repro)

    assert result.patch_applied
    assert result.verdict == V_NOT_REPRODUCED
    assert not result.fixed


def test_unrelated_test_fix_does_not_prove_reproduction_fixed(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )
    (tmp_path / "test_unrelated.py").write_text(
        "from calc import add\n"
        "def test_unrelated():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )

    repro = {
        "test_repro.py": (
            "from calc import add\n"
            "def test_reported_bug():\n"
            "    assert add(2, 3) == 999\n"
        )
    }

    result = verify_patch(str(tmp_path), DIFF, extra_files=repro)

    assert result.patch_applied
    assert result.verdict == V_NOT_FIXED
    assert not result.fixed
    assert result.patched.outcomes
    assert any(
        key.startswith("test_unrelated::") and value == "passed"
        for key, value in result.patched.outcomes.items()
    )
    assert any(
        key.startswith("test_repro::") and value == "failed"
        for key, value in result.patched.outcomes.items()
    )


def test_previously_passing_test_failure_is_reported_as_regression(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n"
        "    return a - b\n"
        "\n"
        "def subtract(a, b):\n"
        "    return a - b\n",
        encoding="utf-8",
    )
    (tmp_path / "test_subtract.py").write_text(
        "from calc import subtract\n"
        "def test_subtract():\n"
        "    assert subtract(5, 3) == 2\n",
        encoding="utf-8",
    )

    diff = (
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,5 +1,5 @@\n"
        " def add(a, b):\n"
        "-    return a - b\n"
        "+    return a + b\n"
        " \n"
        " def subtract(a, b):\n"
        "-    return a - b\n"
        "+    return a + b\n"
    )

    repro = {
        "test_repro.py": (
            "from calc import add\n"
            "def test_reported_bug():\n"
            "    assert add(2, 3) == 5\n"
        )
    }

    result = verify_patch(str(tmp_path), diff, extra_files=repro)

    assert result.patch_applied
    assert result.verdict == V_REGRESSION
    assert not result.fixed
    assert any("test_subtract" in name for name in result.new_failures)



def test_verification_never_modifies_original_repository(tmp_path):
    original = "def add(a, b):\n    return a - b\n"
    source = tmp_path / "calc.py"
    source.write_text(original, encoding="utf-8")

    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )

    result = verify_patch(str(tmp_path), DIFF)

    assert result.patch_applied
    assert result.verdict == V_FIXED
    assert result.fixed
    assert source.read_text(encoding="utf-8") == original



def test_missing_repository_returns_verification_unavailable(tmp_path):
    missing = tmp_path / "does_not_exist"

    result = verify_patch(str(missing), DIFF)

    assert not result.patch_applied
    assert result.verdict == V_UNAVAILABLE
    assert result.fixed is False
    assert result.errors


def test_patch_cannot_modify_generated_reproduction_test():
    diff = (
        "--- a/test_repro.py\n"
        "+++ b/test_repro.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-assert True\n"
        "+assert False\n"
    )

    problems = guard_diff(
        diff,
        extra_files={"test_repro.py": "assert True\n"},
    )

    assert any("reproduction test" in problem for problem in problems)


def test_patch_cannot_modify_pytest_configuration():
    diff = (
        "--- a/pytest.ini\n"
        "+++ b/pytest.ini\n"
        "@@ -1,1 +1,1 @@\n"
        "-[pytest]\n"
        "+[pytest]\n"
    )

    problems = guard_diff(diff)

    assert any("protected file" in problem for problem in problems)


def test_diff_path_traversal_is_rejected():
    diff = (
        "--- a/../../outside.py\n"
        "+++ b/../../outside.py\n"
        "@@ -1,1 +1,1 @@\n"
        "-old\n"
        "+new\n"
    )

    problems = validate_diff_paths(diff)

    assert problems
    assert any("Unsafe path" in problem for problem in problems)


def test_patch_cannot_add_skip_or_xfail_marker():
    diff = (
        "--- a/test_calc.py\n"
        "+++ b/test_calc.py\n"
        "@@ -1,1 +1,2 @@\n"
        " def test_add():\n"
        "+    pytest.skip('skip failing test')\n"
    )

    problems = guard_diff(diff)

    assert any("skip/xfail" in problem for problem in problems)


from sandbox.runner import (
    CommandResult,
    VerificationResult,
    _judge,
)


def _make_judge_result(baseline_outcomes, patched_outcomes,
                       baseline_exit=1, patched_exit=0,
                       baseline_timeout=False, patched_timeout=False):
    baseline = CommandResult(
        command=["pytest"],
        exit_code=baseline_exit,
        output="",
        duration_s=0.1,
        timed_out=baseline_timeout,
        outcomes=baseline_outcomes,
    )
    patched = CommandResult(
        command=["pytest"],
        exit_code=patched_exit,
        output="",
        duration_s=0.1,
        timed_out=patched_timeout,
        outcomes=patched_outcomes,
    )
    return VerificationResult(
        patch_applied=True,
        baseline=baseline,
        patched=patched,
    )



def test_missing_reproduction_test_after_patch_is_not_fixed():
    result = _make_judge_result(
        {"test_repro::test_bug": "failed"},
        {"test_existing::test_other": "passed"},
    )

    _judge(result, ["test_repro"])

    assert result.verdict == V_NOT_FIXED
    assert not result.fixed
    assert any("missing after the patch" in e for e in result.errors)



def test_skipped_reproduction_test_after_patch_is_not_fixed():
    result = _make_judge_result(
        {"test_repro::test_bug": "failed"},
        {"test_repro::test_bug": "skipped"},
    )

    _judge(result, ["test_repro"])

    assert result.verdict == V_NOT_FIXED
    assert not result.fixed
    assert any("skipped after the patch" in e for e in result.errors)


def test_skipped_reproduction_test_before_patch_is_unavailable():
    result = _make_judge_result(
        {"test_repro::test_bug": "skipped"},
        {"test_repro::test_bug": "passed"},
    )

    _judge(result, ["test_repro"])

    assert result.verdict == V_UNAVAILABLE
    assert not result.fixed
    assert any("skipped in the baseline" in e for e in result.errors)


def test_reproduction_collection_error_is_unavailable():
    result = _make_judge_result(
        {"::test_repro.py": "error"},
        {"test_repro::test_bug": "passed"},
    )

    _judge(result, ["test_repro"])

    assert result.verdict == V_UNAVAILABLE
    assert not result.fixed
    assert any("collection" in e for e in result.errors)


def test_baseline_timeout_is_unavailable():
    result = _make_judge_result(
        {"test_repro::test_bug": "failed"},
        {"test_repro::test_bug": "passed"},
        baseline_exit=None,
        baseline_timeout=True,
    )

    _judge(result, ["test_repro"])

    assert result.verdict == V_UNAVAILABLE
    assert not result.fixed
    assert any("timed out" in e for e in result.errors)


def test_patched_run_timeout_is_unavailable():
    result = _make_judge_result(
        {"test_repro::test_bug": "failed"},
        {"test_repro::test_bug": "passed"},
        patched_exit=None,
        patched_timeout=True,
    )

    _judge(result, ["test_repro"])

    assert result.verdict == V_UNAVAILABLE
    assert not result.fixed
    assert any("timed out" in e for e in result.errors)


def test_unexpected_patched_exit_code_is_unavailable():
    result = _make_judge_result(
        {"test_repro::test_bug": "failed"},
        {"test_repro::test_bug": "passed"},
        patched_exit=2,
    )

    _judge(result, ["test_repro"])

    assert result.verdict == V_UNAVAILABLE
    assert not result.fixed
    assert any("patched run exit code" in e for e in result.errors)


def test_missing_junit_outcomes_are_unavailable():
    result = _make_judge_result({}, {})

    _judge(result, ["test_repro"])

    assert result.verdict == V_UNAVAILABLE
    assert not result.fixed
    assert any("no JUnit results" in e for e in result.errors)


def test_previously_passing_test_disappearing_is_regression():
    result = _make_judge_result(
        {
            "test_repro::test_bug": "failed",
            "test_existing::test_existing": "passed",
        },
        {
            "test_repro::test_bug": "passed",
        },
    )

    _judge(result, ["test_repro"])

    assert result.verdict == V_REGRESSION
    assert not result.fixed
    assert "test_existing::test_existing" in result.new_failures


def test_previously_passing_test_becoming_skipped_is_regression():
    result = _make_judge_result(
        {
            "test_repro::test_bug": "failed",
            "test_existing::test_existing": "passed",
        },
        {
            "test_repro::test_bug": "passed",
            "test_existing::test_existing": "skipped",
        },
    )

    _judge(result, ["test_repro"])

    assert result.verdict == V_REGRESSION
    assert not result.fixed
    assert "test_existing::test_existing" in result.new_failures


from sandbox.runner import (
    MAX_LOG_CHARS,
    _clean_env,
    _failed_ids,
    _parse_junit,
    _trim,
    _write_extra,
)


def test_junit_parser_reads_passed_failed_error_and_skipped(tmp_path):
    xml = tmp_path / "results.xml"
    xml.write_text(
        """<?xml version="1.0"?>
        <testsuites>
          <testsuite>
            <testcase classname="test_calc" name="test_pass"/>
            <testcase classname="test_calc" name="test_fail">
              <failure message="assertion failed"/>
            </testcase>
            <testcase classname="test_calc" name="test_error">
              <error message="runtime error"/>
            </testcase>
            <testcase classname="test_calc" name="test_skip">
              <skipped message="skipped"/>
            </testcase>
          </testsuite>
        </testsuites>
        """,
        encoding="utf-8",
    )

    outcomes = _parse_junit(xml)

    assert outcomes == {
        "test_calc::test_pass": "passed",
        "test_calc::test_fail": "failed",
        "test_calc::test_error": "error",
        "test_calc::test_skip": "skipped",
    }


def test_junit_parser_handles_malformed_xml(tmp_path):
    xml = tmp_path / "malformed.xml"
    xml.write_text("<testsuites><testcase>", encoding="utf-8")

    assert _parse_junit(xml) == {}


def test_junit_parser_handles_missing_file(tmp_path):
    assert _parse_junit(tmp_path / "missing.xml") == {}


def test_extra_files_are_written_inside_sandbox(tmp_path):
    _write_extra(
        tmp_path,
        {"tests/generated/test_repro.py": "def test_generated():\n    assert True\n"},
    )

    generated = tmp_path / "tests" / "generated" / "test_repro.py"

    assert generated.is_file()
    assert "test_generated" in generated.read_text(encoding="utf-8")


def test_extra_file_path_traversal_is_rejected(tmp_path):
    try:
        _write_extra(tmp_path, {"../outside.py": "malicious = True\n"})
    except ValueError as exc:
        assert "escapes the sandbox" in str(exc)
    else:
        raise AssertionError("Path traversal should have been rejected")

    assert not (tmp_path.parent / "outside.py").exists()


def test_clean_environment_excludes_api_secrets(monkeypatch, tmp_path):
    monkeypatch.setenv("GROQ_API_KEY", "secret-groq")
    monkeypatch.setenv("OPENAI_API_KEY", "secret-openai")
    monkeypatch.setenv("DATABASE_URL", "secret-database")
    monkeypatch.setenv("PATH", "safe-path")

    env = _clean_env(tmp_path)

    assert "GROQ_API_KEY" not in env
    assert "OPENAI_API_KEY" not in env
    assert "DATABASE_URL" not in env
    assert env["PATH"] == "safe-path"
    assert env["PYTHONPATH"] == str(tmp_path)
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"


def test_trim_preserves_short_output():
    output = "short test output"

    assert _trim(output) == output


def test_trim_limits_long_output_and_preserves_ends():
    output = "A" * (MAX_LOG_CHARS // 2 + 1000) + "MIDDLE" + "Z" * (
        MAX_LOG_CHARS // 2 + 1000
    )

    trimmed = _trim(output)

    assert len(trimmed) <= MAX_LOG_CHARS
    assert trimmed.startswith("A")
    assert trimmed.endswith("Z")
    assert "...[truncated]..." in trimmed


def test_failed_ids_normalizes_paths_and_removes_duplicates():
    output = (
        "FAILED .\\tests\\test_calc.py::test_add - assertion failed\n"
        "FAILED ./tests/test_calc.py::test_add - assertion failed\n"
        "ERROR tests/test_other.py::test_import - import error\n"
    )

    assert _failed_ids(output) == [
        "tests/test_calc.py::test_add",
        "tests/test_other.py::test_import",
    ]


def test_patch_guard_rejects_test_file_deletion():
    diff = (
        "--- a/tests/test_calc.py\n"
        "+++ /dev/null\n"
        "@@ -1,2 +0,0 @@\n"
        "-def test_add():\n"
        "-    assert True\n"
    )

    problems = guard_diff(diff)

    assert any("deletes a test file" in problem for problem in problems)


def test_invalid_diff_is_rejected_without_running_patch(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )
    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )

    invalid_diff = (
        "--- a/../../outside.py\n"
        "+++ b/../../outside.py\n"
        "@@ -1 +1 @@\n"
        "-old\n"
        "+new\n"
    )

    result = verify_patch(str(tmp_path), invalid_diff)

    assert not result.patch_applied
    assert result.verdict == V_NOT_FIXED
    assert result.patched is None
    assert any("Unsafe path" in error for error in result.errors)


def test_patch_that_does_not_apply_is_not_fixed(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )
    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )

    wrong_diff = (
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,2 +1,2 @@\n"
        "-def subtract(a, b):\n"
        "-    return a - b\n"
        "+def subtract(a, b):\n"
        "+    return a + b\n"
    )

    result = verify_patch(str(tmp_path), wrong_diff)

    assert not result.patch_applied
    assert result.verdict == V_NOT_FIXED
    assert result.patched is None
    assert any("failed to apply" in error for error in result.errors)



def test_baseline_with_no_failing_tests_is_not_reproduced(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n",
        encoding="utf-8",
    )
    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n"
        "def test_add():\n"
        "    assert add(2, 3) == -1\n",
        encoding="utf-8",
    )

    result = verify_patch(str(tmp_path), DIFF)

    assert result.patch_applied
    assert result.verdict == V_NOT_REPRODUCED
    assert not result.fixed
    assert any("no failing tests" in error for error in result.errors)



def test_run_baseline_missing_repository_returns_error(tmp_path):
    from sandbox.runner import run_baseline

    result = run_baseline(str(tmp_path / "missing-repo"))

    assert result.exit_code is None
    assert "Repo path not found" in result.output


def test_run_baseline_does_not_modify_original_repository(tmp_path):
    from sandbox.runner import run_baseline

    original = "def add(a, b):\n    return a - b\n"
    source = tmp_path / "calc.py"
    source.write_text(original, encoding="utf-8")
    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )

    result = run_baseline(str(tmp_path))

    assert result.exit_code == 1
    assert source.read_text(encoding="utf-8") == original


def test_verify_patch_reuses_supplied_baseline(tmp_path):
    from sandbox.runner import run_baseline

    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )
    (tmp_path / "test_calc.py").write_text(
        "from calc import add\n"
        "def test_add():\n"
        "    assert add(2, 3) == 5\n",
        encoding="utf-8",
    )

    baseline = run_baseline(str(tmp_path))
    result = verify_patch(str(tmp_path), DIFF, baseline=baseline)

    assert result.baseline is baseline
    assert result.patch_applied
    assert result.verdict == V_FIXED
    assert result.fixed


def test_extra_reproduction_file_is_created_in_baseline_and_verified(tmp_path):
    repro = {
        "test_generated_repro.py": (
            "from calc import add\n"
            "def test_generated_bug():\n"
            "    assert add(2, 3) == 5\n"
        )
    }
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )

    result = verify_patch(str(tmp_path), DIFF, extra_files=repro)

    assert result.patch_applied
    assert result.verdict == V_FIXED
    assert result.fixed
    assert any(
        "test_generated_bug" in name for name in result.fixed_tests
    )


def test_extra_file_path_escape_returns_verification_error(tmp_path):
    repro = {"../outside_repro.py": "def test_bug():\n    assert True\n"}
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )

    try:
        verify_patch(str(tmp_path), DIFF, extra_files=repro)
    except ValueError as exc:
        assert "escapes the sandbox" in str(exc)
    else:
        raise AssertionError(
            "verify_patch should reject an extra file outside the sandbox"
        )


def test_verify_patch_with_no_tests_does_not_claim_fixed(tmp_path):
    (tmp_path / "calc.py").write_text(
        "def add(a, b):\n    return a - b\n", encoding="utf-8"
    )

    result = verify_patch(str(tmp_path), DIFF)

    assert result.patch_applied
    assert result.verdict == V_UNAVAILABLE
    assert not result.fixed
    assert result.patched is not None


def test_verify_patch_with_repro_does_not_modify_original_repository(tmp_path):
    original = "def add(a, b):\n    return a - b\n"
    source = tmp_path / "calc.py"
    source.write_text(original, encoding="utf-8")

    repro = {
        "test_generated_repro.py": (
            "from calc import add\n"
            "def test_generated_bug():\n"
            "    assert add(2, 3) == 5\n"
        )
    }

    result = verify_patch(str(tmp_path), DIFF, extra_files=repro)

    assert result.verdict == V_FIXED
    assert result.fixed
    assert source.read_text(encoding="utf-8") == original
    assert not (tmp_path / "test_generated_repro.py").exists()

