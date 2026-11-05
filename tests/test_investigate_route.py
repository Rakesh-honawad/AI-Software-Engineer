
from types import SimpleNamespace
from auth.dependencies import AuthenticatedUser

from api.routes.investigate import (
    _canon,
    edits_touch_tests,
    evidence_was_fixed,
    find_syntax_errors,
    related_failures,
    report_names_symbol,
)


def make_node(name, file_path="src/cart.py", is_deleted=False):
    return SimpleNamespace(
        name=name,
        file_path=file_path,
        is_deleted=is_deleted,
    )


def make_suspect(name, file_path="src/cart.py"):
    return SimpleNamespace(node=make_node(name, file_path))


def test_report_names_snake_case_symbol():
    nodes = [make_node("calculate_discount")]

    assert report_names_symbol(
        "The calculate_discount function returns the wrong value",
        nodes,
    )


def test_report_names_plain_symbol_when_called():
    nodes = [make_node("restock")]

    assert report_names_symbol(
        "The restock() function fails",
        nodes,
    )


def test_report_does_not_match_ordinary_single_word():
    nodes = [make_node("restock")]

    assert not report_names_symbol(
        "The inventory restock operation is incorrect",
        nodes,
    )


def test_report_names_ignores_test_and_deleted_nodes():
    nodes = [
        make_node("calculate_discount", "tests/test_cart.py"),
        make_node("calculate_discount", "src/old_cart.py", is_deleted=True),
    ]

    assert not report_names_symbol(
        "calculate_discount returns the wrong value",
        nodes,
    )


def test_edits_touch_tests_blocks_test_and_config_files():
    edits = [
        {"file": "src/cart.py"},
        {"file": "tests/test_cart.py"},
        {"file": "pytest.ini"},
        {"file": "config/setup.cfg"},
    ]

    assert edits_touch_tests(edits) == [
        "tests/test_cart.py",
        "pytest.ini",
        "config/setup.cfg",
    ]


def test_canon_normalizes_console_and_junit_test_ids():
    assert _canon("tests/test_cart.py::TestCart::test_discount") == (
        "tests.test_cart.TestCart.test_discount"
    )
    assert _canon("tests.test_cart.TestCart::test_discount") == (
        "tests.test_cart.TestCart.test_discount"
    )


def test_evidence_was_fixed_requires_fixed_reproduction_verdict():
    assert evidence_was_fixed(
        SimpleNamespace(verdict="fixed"),
        reproduced=True,
        has_related_failure=False,
        related_tests=[],
    )

    assert not evidence_was_fixed(
        SimpleNamespace(verdict="regression"),
        reproduced=True,
        has_related_failure=False,
        related_tests=[],
    )


def test_evidence_was_fixed_requires_the_related_test_itself():
    verification = SimpleNamespace(
        fixed=True,
        fixed_tests=["tests.test_cart.TestCart.test_other"],
    )

    assert not evidence_was_fixed(
        verification,
        reproduced=False,
        has_related_failure=True,
        related_tests=["tests/test_cart.py::TestCart::test_discount"],
    )

    verification.fixed_tests = ["tests.test_cart.TestCart.test_discount"]

    assert evidence_was_fixed(
        verification,
        reproduced=False,
        has_related_failure=True,
        related_tests=["tests/test_cart.py::TestCart::test_discount"],
    )


def test_related_failures_matches_traceback_path():
    suspects = [make_suspect("calculate_discount", "src/cart.py")]
    output = (
        "___ tests/test_cart.py::test_discount ___\n"
        "Traceback (most recent call last):\n"
        '  File "src/cart.py", line 12, in calculate_discount\n'
        "ValueError: invalid discount\n"
        "FAILED tests/test_cart.py::test_discount\n"
    )

    related_text, related_ids, unrelated_ids = related_failures(
        output, suspects
    )

    assert "ValueError: invalid discount" in related_text
    assert related_ids == ["tests/test_cart.py::test_discount"]
    assert unrelated_ids == []


def test_find_syntax_errors_reports_python_errors_and_skips_ignored_dirs(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "broken.py").write_text(
        "def broken(:\n    pass\n",
        encoding="utf-8",
    )

    ignored = tmp_path / ".venv" / "Lib"
    ignored.mkdir(parents=True)
    (ignored / "also_broken.py").write_text(
        "def broken(:\n    pass\n",
        encoding="utf-8",
    )

    errors = find_syntax_errors(str(tmp_path))

    assert len(errors) == 1
    assert errors[0]["file"] == "src/broken.py"
    assert errors[0]["line"] == 1


import asyncio

import pytest
from fastapi import HTTPException

import api.routes.investigate as investigate_module
from api.routes.investigate import InvestigateRequest, investigate
from sandbox.runner import CommandResult


def call_investigate(repository_id="repo-1", request=None):
    return asyncio.run(
        investigate(
            repository_id,
            request or InvestigateRequest(bug_report="calculate_discount is wrong"),
            AuthenticatedUser(
                id="test-user",
                email="test@example.com",
                claims={},
            ),
        )
    )


def successful_baseline():
    return CommandResult(
        command=["pytest"],
        exit_code=0,
        output="",
        duration_s=0.1,
    )


def make_route_suspect():
    node = make_node("calculate_discount", "src/cart.py")
    return SimpleNamespace(
        node=node,
        to_dict=lambda: {
            "name": "calculate_discount",
            "file_path": "src/cart.py",
        },
    )


def prepare_route(monkeypatch, tmp_path, baseline=None, suspects=None):
    monkeypatch.setattr(
        investigate_module,
        "load_graph_for_repo",
        lambda repo_id, owner_id: ([], []),
    )
    monkeypatch.setattr(
        investigate_module,
        "get_storage_dir_or_404",
        lambda repo_id, owner_id: str(tmp_path),
    )
    monkeypatch.setattr(
        investigate_module,
        "load_record",
        lambda storage: SimpleNamespace(local_path=str(tmp_path), owner_id="test-user"),
    )
    monkeypatch.setattr(
        investigate_module,
        "find_syntax_errors",
        lambda path: [],
    )
    monkeypatch.setattr(
        investigate_module,
        "run_baseline",
        lambda *args, **kwargs: baseline or successful_baseline(),
    )
    monkeypatch.setattr(
        investigate_module,
        "localize",
        lambda *args, **kwargs: suspects if suspects is not None else [],
    )


@pytest.mark.parametrize(
    "test_path",
    [
        "../outside/test.py",
        "/absolute/test.py",
        r"C:\outside\test.py",
        "-q",
    ],
)
def test_rejects_unsafe_test_paths(test_path, monkeypatch, tmp_path):
    prepare_route(monkeypatch, tmp_path)
    with pytest.raises(HTTPException) as exc:
        call_investigate(
            request=InvestigateRequest(
                bug_report="calculate_discount is wrong",
                test_path=test_path,
            )
        )

    assert exc.value.status_code == 400


def test_missing_repository_record_returns_404(monkeypatch, tmp_path):
    monkeypatch.setattr(
        investigate_module,
        "load_graph_for_repo",
        lambda repo_id, owner_id: ([], []),
    )
    monkeypatch.setattr(
        investigate_module,
        "get_storage_dir_or_404",
        lambda repo_id, owner_id: str(tmp_path),
    )
    monkeypatch.setattr(
        investigate_module,
        "load_record",
        lambda storage: None,
    )

    with pytest.raises(HTTPException) as exc:
        call_investigate()

    assert exc.value.status_code == 404


def test_baseline_timeout_returns_tests_could_not_run(
    monkeypatch, tmp_path
):
    timed_out = CommandResult(
        command=["pytest"],
        exit_code=None,
        output="Timed out",
        duration_s=120.0,
        timed_out=True,
    )
    prepare_route(monkeypatch, tmp_path, baseline=timed_out)

    result = call_investigate()

    assert result["status"] == "tests_could_not_run"
    assert result["attempts"] == []
    assert result["suspects"] == []


def test_syntax_error_in_suspect_file_blocks_investigation(
    monkeypatch, tmp_path
):
    suspect = make_route_suspect()
    prepare_route(
        monkeypatch,
        tmp_path,
        suspects=[suspect],
    )
    monkeypatch.setattr(
        investigate_module,
        "find_syntax_errors",
        lambda path: [
            {
                "file": "src/cart.py",
                "line": 12,
                "message": "invalid syntax",
                "text": "def calculate_discount(:",
            }
        ],
    )

    result = call_investigate()

    assert result["status"] == "syntax_error"
    assert result["attempts"] == []
    assert result["syntax_errors"][0]["file"] == "src/cart.py"


def test_no_suspects_returns_without_generating_reproduction(
    monkeypatch, tmp_path
):
    prepare_route(monkeypatch, tmp_path, suspects=[])

    def unexpected_repro(*args, **kwargs):
        raise AssertionError("Reproduction must not run without suspects")

    monkeypatch.setattr(
        investigate_module,
        "generate_repro",
        unexpected_repro,
    )

    result = call_investigate()

    assert result["status"] == "no_suspects"
    assert result["suspects"] == []
    assert result["attempts"] == []


def test_unreproducible_report_does_not_generate_patch(
    monkeypatch, tmp_path
):
    suspect = make_route_suspect()
    prepare_route(
        monkeypatch,
        tmp_path,
        suspects=[suspect],
    )

    async def fake_generate_repro(*args, **kwargs):
        return SimpleNamespace(
            not_reproducible=True,
            reason="The report does not provide enough detail.",
            error=None,
        )

    def unexpected_patch(*args, **kwargs):
        raise AssertionError("Patch proposal must not run without evidence")

    monkeypatch.setattr(
        investigate_module,
        "generate_repro",
        fake_generate_repro,
    )
    monkeypatch.setattr(
        investigate_module,
        "propose_patch",
        unexpected_patch,
    )

    result = call_investigate()

    assert result["status"] == "no_patch"
    assert result["reproduction"]["status"] == "not_reproducible"
    assert result["attempts"] == []
    assert result["diff"] == ""


def test_rejected_generated_test_does_not_generate_patch(
    monkeypatch, tmp_path
):
    suspect = make_route_suspect()
    prepare_route(
        monkeypatch,
        tmp_path,
        suspects=[suspect],
    )

    async def fake_generate_repro(*args, **kwargs):
        return SimpleNamespace(
            not_reproducible=False,
            reason="",
            error="Generated test failed validation.",
        )

    def unexpected_patch(*args, **kwargs):
        raise AssertionError("Rejected reproduction must not reach patching")

    monkeypatch.setattr(
        investigate_module,
        "generate_repro",
        fake_generate_repro,
    )
    monkeypatch.setattr(
        investigate_module,
        "propose_patch",
        unexpected_patch,
    )

    result = call_investigate()

    assert result["status"] == "no_patch"
    assert result["reproduction"]["status"] == "invalid_test"
    assert result["attempts"] == []
    assert result["diff"] == ""


def failure_section(test_id, body):
    return (
        f"_____ {test_id} _____\n"
        f"{body}\n"
        f"FAILED {test_id}\n"
    )


def test_related_failure_matches_suspect_symbol():
    suspects = [make_suspect("calculate_discount")]
    output = failure_section(
        "tests/test_cart.py::test_discount",
        "AssertionError in calculate_discount",
    )

    related, ids, unrelated = related_failures(output, suspects)

    assert "calculate_discount" in related
    assert ids == ["tests/test_cart.py::test_discount"]
    assert unrelated == []


def test_unrelated_failure_is_separated():
    suspects = [make_suspect("calculate_discount")]
    output = failure_section(
        "tests/test_user.py::test_login",
        "AssertionError in authenticate_user",
    )

    related, ids, unrelated = related_failures(output, suspects)

    assert related == ""
    assert ids == []
    assert unrelated == ["tests/test_user.py::test_login"]


def test_multiple_sections_are_classified_independently():
    suspects = [make_suspect("calculate_discount")]
    output = (
        failure_section(
            "tests/test_cart.py::test_discount",
            "AssertionError in calculate_discount",
        )
        + failure_section(
            "tests/test_user.py::test_login",
            "AssertionError in authenticate_user",
        )
    )

    related, ids, unrelated = related_failures(output, suspects)

    assert len(ids) == 1
    assert ids[0] == "tests/test_cart.py::test_discount"
    assert len(unrelated) == 1
    assert unrelated[0] == "tests/test_user.py::test_login"


def test_related_failure_matches_suspect_file_path():
    suspects = [make_suspect("calculate_discount", "src/cart.py")]
    output = failure_section(
        "tests/test_cart.py::test_discount",
        'File "src/cart.py", line 42, in helper',
    )

    related, ids, unrelated = related_failures(output, suspects)

    assert "src/cart.py" in related
    assert ids == ["tests/test_cart.py::test_discount"]
    assert unrelated == []


def test_related_failure_normalizes_windows_traceback_path():
    suspects = [make_suspect("calculate_discount", "src/cart.py")]
    output = failure_section(
        "tests/test_cart.py::test_discount",
        r'File "C:\project\src\cart.py", line 42, in helper',
    )

    related, ids, unrelated = related_failures(output, suspects)

    assert related
    assert ids == ["tests/test_cart.py::test_discount"]
    assert unrelated == []


def test_symbol_match_uses_word_boundaries():
    suspects = [make_suspect("calculate_discount")]
    output = failure_section(
        "tests/test_cart.py::test_discount",
        "AssertionError in calculate_discount_extra",
    )

    related, ids, unrelated = related_failures(output, suspects)

    assert related == ""
    assert ids == []
    assert unrelated == ["tests/test_cart.py::test_discount"]


def test_only_top_three_suspects_are_used_for_relevance():
    suspects = [
        make_suspect("first_function", "src/first.py"),
        make_suspect("second_function", "src/second.py"),
        make_suspect("third_function", "src/third.py"),
        make_suspect("fourth_function", "src/fourth.py"),
    ]
    output = failure_section(
        "tests/test_feature.py::test_fourth",
        "AssertionError in fourth_function",
    )

    related, ids, unrelated = related_failures(output, suspects)

    assert related == ""
    assert ids == []
    assert unrelated == ["tests/test_feature.py::test_fourth"]


def test_no_failure_section_returns_empty_classification():
    suspects = [make_suspect("calculate_discount")]

    related, ids, unrelated = related_failures(
        "2 failed, 5 passed",
        suspects,
    )

    assert related == ""
    assert ids == []
    assert unrelated == []


def test_related_failure_uses_section_name_when_failed_line_is_missing():
    suspects = [make_suspect("calculate_discount")]
    output = (
        "_____ tests/test_cart.py::test_discount _____\n"
        "AssertionError in calculate_discount\n"
    )

    related, ids, unrelated = related_failures(output, suspects)

    assert related
    assert ids == ["tests/test_cart.py::test_discount"]
    assert unrelated == []


def test_route_skips_reproduction_when_existing_failure_is_related(
    monkeypatch, tmp_path
):
    suspect = make_route_suspect()
    test_id = "tests/test_cart.py::test_discount"
    output = failure_section(
        test_id,
        "AssertionError in calculate_discount",
    )
    baseline = CommandResult(
        command=["pytest"],
        exit_code=1,
        output=output,
        duration_s=0.2,
    )

    prepare_route(
        monkeypatch,
        tmp_path,
        baseline=baseline,
        suspects=[suspect],
    )
    monkeypatch.setattr(
        investigate_module,
        "load_graph_for_repo",
        lambda repo_id, owner_id: ([make_node("calculate_discount")], []),
    )

    async def unexpected_repro(*args, **kwargs):
        raise AssertionError(
            "An existing related failure should be used as evidence"
        )

    proposal = SimpleNamespace(
        error=None,
        verdict="test_or_report_wrong",
        verdict_reason="Needs manual review",
        edits=[],
        root_cause="Needs investigation",
        explanation="The failure requires review.",
        confidence="low",
    )

    async def fake_propose_patch(*args, **kwargs):
        return proposal

    monkeypatch.setattr(
        investigate_module,
        "generate_repro",
        unexpected_repro,
    )
    monkeypatch.setattr(
        investigate_module,
        "propose_patch",
        fake_propose_patch,
    )

    result = call_investigate()

    assert result["evidence"] == "existing_failing_test"
    assert result["reproduction"]["status"] == "skipped"
    assert result["failing_tests"] == [test_id]
    assert result["attempts"][0]["verdict"] == "test_or_report_wrong"

def test_vague_report_does_not_localize_from_unrelated_failure(
    monkeypatch, tmp_path
):
    bug_report = "Shipping for a 5 kg parcel should cost $25."

    suspect = SimpleNamespace(
        node=make_node("shipping_cost", "shipping.py"),
        to_dict=lambda: {
            "name": "shipping_cost",
            "file_path": "shipping.py",
        },
    )

    unrelated_id = "tests/test_shop.py::test_tax"
    output = failure_section(
        unrelated_id,
        "AssertionError in calculate_tax",
    )
    baseline = CommandResult(
        command=["pytest"],
        exit_code=1,
        output=output,
        duration_s=0.2,
    )

    prepare_route(
        monkeypatch,
        tmp_path,
        baseline=baseline,
        suspects=[suspect],
    )

    monkeypatch.setattr(
        investigate_module,
        "load_graph_for_repo",
        lambda repo_id, owner_id: ([make_node("shipping_cost", "shipping.py")], []),
    )

    localization_inputs = []

    def capture_localize(
        report, failure_output, nodes, edges, **kwargs
    ):
        localization_inputs.append(failure_output)
        return [suspect]

    monkeypatch.setattr(
        investigate_module,
        "localize",
        capture_localize,
    )

    repro_calls = []

    async def fake_generate_repro(*args, **kwargs):
        repro_calls.append(True)
        return SimpleNamespace(
            not_reproducible=True,
            reason="Could not reproduce the reported shipping behavior.",
            error=None,
        )

    def unexpected_patch(*args, **kwargs):
        raise AssertionError(
            "An unrelated tax failure must not justify a shipping patch"
        )

    monkeypatch.setattr(
        investigate_module,
        "generate_repro",
        fake_generate_repro,
    )
    monkeypatch.setattr(
        investigate_module,
        "propose_patch",
        unexpected_patch,
    )

    result = call_investigate(
        request=InvestigateRequest(bug_report=bug_report)
    )

    assert localization_inputs
    assert all(not text for text in localization_inputs)
    assert repro_calls
    assert result["unrelated_failures"] == [unrelated_id]
    assert result["reproduction"]["status"] == "not_reproducible"
    assert result["status"] == "no_patch"
    assert result["attempts"] == []

def test_route_does_not_use_unrelated_failure_as_evidence(
    monkeypatch, tmp_path
):
    suspect = make_route_suspect()
    unrelated_id = "tests/test_user.py::test_login"
    output = failure_section(
        unrelated_id,
        "AssertionError in authenticate_user",
    )
    baseline = CommandResult(
        command=["pytest"],
        exit_code=1,
        output=output,
        duration_s=0.2,
    )

    prepare_route(
        monkeypatch,
        tmp_path,
        baseline=baseline,
        suspects=[suspect],
    )
    monkeypatch.setattr(
        investigate_module,
        "load_graph_for_repo",
        lambda repo_id, owner_id: ([make_node("calculate_discount")], []),
    )

    async def fake_generate_repro(*args, **kwargs):
        return SimpleNamespace(
            not_reproducible=True,
            reason="Could not reproduce the reported behavior.",
            error=None,
        )

    def unexpected_patch(*args, **kwargs):
        raise AssertionError(
            "An unrelated failure must not justify patch generation"
        )

    monkeypatch.setattr(
        investigate_module,
        "generate_repro",
        fake_generate_repro,
    )
    monkeypatch.setattr(
        investigate_module,
        "propose_patch",
        unexpected_patch,
    )

    result = call_investigate()

    assert result["status"] == "no_patch"
    assert result["evidence"] if "evidence" in result else "none"
    assert result["unrelated_failures"] == [unrelated_id]
    assert result["reproduction"]["status"] == "not_reproducible"
    assert result["attempts"] == []


def make_patch_proposal(
    edits=None,
    verdict="code_bug",
    error=None,
):
    proposal_edits = edits if edits is not None else [
        {
            "file": "src/cart.py",
            "content": "def calculate_discount(): return 10",
        }
    ]

    return SimpleNamespace(
        error=error,
        verdict=verdict,
        verdict_reason="Test proposal",
        edits=proposal_edits,
        allowed_files=[
            edit["file"] for edit in proposal_edits
        ],
        root_cause="Incorrect discount calculation",
        explanation="The calculation uses the wrong value.",
        confidence="high",
    )


def make_verification(
    verdict="fixed",
    fixed=True,
    fixed_tests=None,
    still_failing=None,
    new_failures=None,
):
    return SimpleNamespace(
        patch_applied=True,
        apply_log="patch applied",
        patched=successful_baseline(),
        fixed=fixed,
        verdict=verdict,
        errors=[],
        fixed_tests=fixed_tests or [],
        still_failing=still_failing or [],
        new_failures=new_failures or [],
    )


def prepare_patch_workflow(monkeypatch, tmp_path, verification):
    test_id = "tests/test_cart.py::test_discount"

    output = failure_section(
        test_id,
        "AssertionError in calculate_discount",
    )
    baseline = CommandResult(
        command=["pytest"],
        exit_code=1,
        output=output,
        duration_s=0.2,
    )

    prepare_route(
        monkeypatch,
        tmp_path,
        baseline=baseline,
        suspects=[make_route_suspect()],
    )

    monkeypatch.setattr(
        investigate_module,
        "build_diff",
        lambda *args, **kwargs: ("generated-diff", []),
    )
    monkeypatch.setattr(
        investigate_module,
        "patched_contents",
        lambda *args, **kwargs: [
            {"file": "src/cart.py", "content": "patched content"}
        ],
    )
    monkeypatch.setattr(
        investigate_module,
        "verify_patch",
        lambda *args, **kwargs: verification,
    )

    async def fake_propose(*args, **kwargs):
        return make_patch_proposal()

    monkeypatch.setattr(
        investigate_module,
        "propose_patch",
        fake_propose,
    )

    return test_id


def test_integration_related_failure_patch_verified_fixed(
    monkeypatch, tmp_path
):
    test_id = "tests/test_cart.py::test_discount"
    verification = make_verification(
        verdict="fixed",
        fixed=True,
        fixed_tests=[test_id],
    )

    prepare_patch_workflow(monkeypatch, tmp_path, verification)

    result = call_investigate()

    assert result["status"] == "fixed"
    assert result["evidence"] == "existing_failing_test"
    assert result["verification_verdict"] == "fixed"
    assert result["attempts"][0]["verification"]["fixed"] is True
    assert result["diff"] == "generated-diff"


def test_integration_unrelated_test_fix_does_not_prove_bug_fixed(
    monkeypatch, tmp_path
):
    verification = make_verification(
        verdict="fixed",
        fixed=True,
        fixed_tests=["tests.test_user.test_login"],
    )

    prepare_patch_workflow(monkeypatch, tmp_path, verification)

    result = call_investigate()

    assert result["status"] == "not_fixed"
    assert result["attempts"][0]["verification"]["suite_improved"] is True
    assert result["attempts"][0]["verification"]["fixed"] is False


def test_integration_regression_is_not_reported_as_fixed(
    monkeypatch, tmp_path
):
    verification = make_verification(
        verdict="regression",
        fixed=False,
        fixed_tests=[],
        new_failures=["tests.test_cart.test_other"],
    )

    prepare_patch_workflow(monkeypatch, tmp_path, verification)

    result = call_investigate()

    assert result["status"] == "not_fixed"
    assert result["verification_verdict"] == "regression"
    assert result["attempts"][0]["verification"]["fixed"] is False
    assert result["attempts"][0]["verification"]["new_failures"] == [
        "tests.test_cart.test_other"
    ]


def test_integration_verification_unavailable_is_not_fixed(
    monkeypatch, tmp_path
):
    verification = make_verification(
        verdict="verification_unavailable",
        fixed=False,
    )

    prepare_patch_workflow(monkeypatch, tmp_path, verification)

    result = call_investigate()

    assert result["status"] == "verification_unavailable"
    assert result["verification_verdict"] == "verification_unavailable"
    assert result["attempts"][0]["verification"]["fixed"] is False


def test_integration_patch_cannot_modify_test_files(
    monkeypatch, tmp_path
):
    verification = make_verification()

    prepare_patch_workflow(monkeypatch, tmp_path, verification)

    async def fake_propose(*args, **kwargs):
        return make_patch_proposal(
            edits=[
                {
                    "file": "tests/test_cart.py",
                    "content": "def test_discount(): assert True",
                }
            ]
        )

    monkeypatch.setattr(
        investigate_module,
        "propose_patch",
        fake_propose,
    )

    def unexpected_verify(*args, **kwargs):
        raise AssertionError("Protected edits must never reach verification")

    monkeypatch.setattr(
        investigate_module,
        "verify_patch",
        unexpected_verify,
    )

    result = call_investigate()

    assert result["status"] == "no_patch"
    assert len(result["attempts"]) == 2
    assert all(a["verification"] is None for a in result["attempts"])
    assert all("edits tests" in a["error"] for a in result["attempts"])


def test_integration_invalid_patch_proposal_never_reaches_verification(
    monkeypatch, tmp_path
):
    verification = make_verification()

    prepare_patch_workflow(monkeypatch, tmp_path, verification)

    async def fake_propose(*args, **kwargs):
        return make_patch_proposal(
            error="The model returned invalid edits."
        )

    monkeypatch.setattr(
        investigate_module,
        "propose_patch",
        fake_propose,
    )

    def unexpected_verify(*args, **kwargs):
        raise AssertionError("Invalid proposals must not be verified")

    monkeypatch.setattr(
        investigate_module,
        "verify_patch",
        unexpected_verify,
    )

    result = call_investigate()

    assert result["status"] == "no_patch"
    assert len(result["attempts"]) == 2
    assert all(
        a["error"] == "The model returned invalid edits."
        for a in result["attempts"]
    )
    assert all(a["verification"] is None for a in result["attempts"])


def test_integration_empty_diff_is_rejected(
    monkeypatch, tmp_path
):
    verification = make_verification()

    prepare_patch_workflow(monkeypatch, tmp_path, verification)

    monkeypatch.setattr(
        investigate_module,
        "build_diff",
        lambda *args, **kwargs: ("", []),
    )

    def unexpected_verify(*args, **kwargs):
        raise AssertionError("An empty diff must not be verified")

    monkeypatch.setattr(
        investigate_module,
        "verify_patch",
        unexpected_verify,
    )

    result = call_investigate()

    assert result["status"] == "no_patch"
    assert len(result["attempts"]) == 2
    assert all(
        a["error"] == "The model proposed no change."
        for a in result["attempts"]
    )


def test_integration_second_attempt_can_fix_first_attempt_failure(
    monkeypatch, tmp_path
):
    test_id = "tests/test_cart.py::test_discount"

    first = make_verification(
        verdict="not_fixed",
        fixed=False,
    )
    second = make_verification(
        verdict="fixed",
        fixed=True,
        fixed_tests=[test_id],
    )

    prepare_patch_workflow(monkeypatch, tmp_path, first)

    verification_results = iter([first, second])
    monkeypatch.setattr(
        investigate_module,
        "verify_patch",
        lambda *args, **kwargs: next(verification_results),
    )

    proposals = []

    async def fake_propose(*args, **kwargs):
        proposals.append(len(proposals) + 1)
        return make_patch_proposal()

    monkeypatch.setattr(
        investigate_module,
        "propose_patch",
        fake_propose,
    )

    result = call_investigate()

    assert proposals == [1, 2]
    assert len(result["attempts"]) == 2
    assert result["attempts"][0]["verification"]["verdict"] == "not_fixed"
    assert result["attempts"][1]["verification"]["fixed"] is True
    assert result["status"] == "fixed"


def test_integration_retry_limit_is_two_attempts(
    monkeypatch, tmp_path
):
    verification = make_verification(
        verdict="not_fixed",
        fixed=False,
    )

    prepare_patch_workflow(monkeypatch, tmp_path, verification)

    proposal_count = []

    async def fake_propose(*args, **kwargs):
        proposal_count.append(1)
        return make_patch_proposal()

    monkeypatch.setattr(
        investigate_module,
        "propose_patch",
        fake_propose,
    )

    result = call_investigate()

    assert len(proposal_count) == 2
    assert len(result["attempts"]) == 2
    assert result["status"] == "not_fixed"
