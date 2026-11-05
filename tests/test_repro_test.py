
from types import SimpleNamespace

import pytest
from ai.reasoning.repro_test import validate_repro_structure


from ai.reasoning.repro_test import (
    REPRO_FILE,
    _error_raised_in_repo_code,
    _is_repo_frame,
    check_repro_code,
    classify_repro_run,
    quote_is_grounded,
    repro_touches_suspects,
    validate_repro_structure,
)

from types import SimpleNamespace

from ai.reasoning.repro_test import (
    _is_repo_frame,
    _error_raised_in_repo_code,
    repro_touches_suspects,
)

def make_repro_result(
    exit_code=1,
    output="",
    timed_out=False,
):
    return SimpleNamespace(
        exit_code=exit_code,
        output=output,
        timed_out=timed_out,
    )


def make_repro_suspect(name="calculate_discount", path="src/cart.py"):
    node = SimpleNamespace(name=name, file_path=path)
    return SimpleNamespace(node=node)


def test_rejects_empty_reproduction_code():
    assert check_repro_code("") == "empty test"


def test_rejects_reproduction_without_test_function():
    problem = check_repro_code(
        "from app.cart import calculate_discount\n"
        "value = calculate_discount(10)\n"
    )

    assert problem == "no test_ function found"


def test_rejects_blocked_imports():
    problem = check_repro_code(
        "import subprocess\n"
        "def test_bug():\n"
        "    assert True\n"
    )

    assert problem == "test imports a blocked module: ['subprocess']"


def test_rejects_test_that_does_not_call_suspect_code():
    code = (
        "def test_discount():\n"
        "    assert 10 == 10\n"
    )

    problem = validate_repro_structure(
        code,
        ["src.cart"],
        {"calculate_discount"},
    )

    assert problem == "the test never calls code from the files under test"


def test_accepts_direct_import_of_suspect_function():
    code = (
        "from src.cart import calculate_discount\n"
        "def test_discount():\n"
        "    assert calculate_discount(10) == 5\n"
    )

    assert validate_repro_structure(
        code,
        ["src.cart"],
        {"calculate_discount"},
    ) is None


def test_rejects_unconditional_assert_false():
    code = (
        "from src.cart import calculate_discount\n"
        "def test_discount():\n"
        "    calculate_discount(10)\n"
        "    assert False\n"
    )

    problem = validate_repro_structure(
        code,
        ["src.cart"],
        {"calculate_discount"},
    )

    assert "fails unconditionally" in problem


def test_rejects_ungrounded_or_short_report_quotes():
    report = "The calculate_discount function returns 5 instead of 10."

    assert quote_is_grounded(
        "calculate_discount function returns 5",
        report,
    )

    assert not quote_is_grounded(
        "The discount must be 20 percent",
        report,
    )

    assert not quote_is_grounded(
        "discount",
        report,
    )


def test_classify_repro_rejects_passed_test():
    result = make_repro_result(
        exit_code=0,
        output="1 passed",
    )

    ok, reason = classify_repro_run(result)

    assert not ok
    assert "PASSED" in reason


def test_classify_repro_rejects_unrelated_test_failure():
    result = make_repro_result(
        exit_code=1,
        output=(
            "FAILED tests/test_cart.py::test_discount\n"
            "AssertionError: wrong value\n"
        ),
    )

    ok, reason = classify_repro_run(result)

    assert not ok
    assert "different test failed" in reason


def test_classify_repro_accepts_generated_assertion_failure():
    result = make_repro_result(
        exit_code=1,
        output=(
            f"FAILED {REPRO_FILE}::test_discount\n"
            "E assert 5 == 10\n"
        ),
    )

    ok, reason = classify_repro_run(result)

    assert ok
    assert reason == "reproduced"

def test_classify_rejects_timed_out_reproduction():
    result = SimpleNamespace(
        exit_code=1,
        timed_out=True,
        output="FAILED test_miniai_repro.py::test_bug",
    )
    assert classify_repro_run(result)[0] is False


def test_classify_rejects_missing_exit_code():
    result = SimpleNamespace(
        exit_code=None,
        timed_out=False,
        output="FAILED test_miniai_repro.py::test_bug",
    )
    assert classify_repro_run(result)[0] is False


def test_classify_rejects_unexpected_exit_code():
    result = SimpleNamespace(
        exit_code=2,
        timed_out=False,
        output="FAILED test_miniai_repro.py::test_bug\nAssertionError",
    )
    assert classify_repro_run(result)[0] is False


def test_classify_rejects_failure_from_another_test_file():
    result = SimpleNamespace(
        exit_code=1,
        timed_out=False,
        output=(
            "FAILED tests/test_existing.py::test_existing_bug "
            "- AssertionError: expected value"
        ),
    )
    assert classify_repro_run(result)[0] is False


def test_classify_rejects_import_error_without_repository_frame():
    result = SimpleNamespace(
        exit_code=1,
        timed_out=False,
        output="ImportError: cannot import name 'missing' from 'some_package'",
    )
    assert classify_repro_run(result)[0] is False


def test_classify_rejects_name_error_without_repository_frame():
    result = SimpleNamespace(
        exit_code=1,
        timed_out=False,
        output=(
            "NameError: name 'missing_value' is not defined\n"
            "test_miniai_repro.py:12"
        ),
    )
    assert classify_repro_run(result)[0] is False


def test_classify_accepts_did_not_raise_from_reproduction():
    result = SimpleNamespace(
        exit_code=1,
        timed_out=False,
        output=(
            "FAILED test_miniai_repro.py::test_bug "
            "- Failed: DID NOT RAISE <class 'ValueError'>"
        ),
    )
    assert classify_repro_run(result)[0] is True


def test_classify_accepts_assertion_failure_from_repository_code():
    result = SimpleNamespace(
        exit_code=1,
        timed_out=False,
        output=(
            "FAILED test_miniai_repro.py::test_bug\n"
            "AssertionError: expected 10, got 5\n"
            'File "src/cart.py", line 42, in calculate_total'
        ),
    )
    assert classify_repro_run(result)[0] is True


def test_classify_accepts_exception_with_repository_frame():
    result = SimpleNamespace(
        exit_code=1,
        timed_out=False,
        output=(
            "FAILED test_miniai_repro.py::test_bug\n"
            "ValueError: invalid price\n"
            'File "src/cart.py", line 42, in calculate_total'
        ),
    )
    assert classify_repro_run(result)[0] is True


def test_classify_accepts_reproduction_traceback_without_failed_line():
    result = SimpleNamespace(
        exit_code=1,
        timed_out=False,
        output=(
            "Traceback (most recent call last):\n"
            "test_miniai_repro.py:8: in test_bug\n"
            "src/cart.py:42: in calculate_total\n"
            "AssertionError: incorrect total"
        ),
    )
    assert classify_repro_run(result)[0] is True

# Repository frame detection

def test_repo_frame_accepts_relative_application_path():
    assert _is_repo_frame("src/cart.py") is True


def test_repo_frame_accepts_windows_relative_application_path():
    assert _is_repo_frame(r"src\cart.py") is True


def test_repo_frame_rejects_absolute_posix_path():
    assert _is_repo_frame("/usr/local/project/src/cart.py") is False


def test_repo_frame_rejects_windows_absolute_path():
    assert _is_repo_frame(r"C:\project\src\cart.py") is False


def test_repo_frame_rejects_site_packages():
    assert _is_repo_frame("site-packages/example/module.py") is False


def test_repo_frame_rejects_test_file():
    assert _is_repo_frame("tests/test_cart.py") is False


# Exception origin detection


def test_error_detector_rejects_exception_from_library_frame():
    output = (
        '  File "site-packages/example/module.py", line 42, in run\n'
        "ValueError: invalid price"
    )
    assert _error_raised_in_repo_code(output) is False


# Suspect matching

def test_repro_touches_suspects_when_traceback_matches_suspect():
    suspects = [
        SimpleNamespace(
            node=SimpleNamespace(file_path="src/cart.py")
        )
    ]
    output = (
        '  File "src/cart.py", line 42, in calculate_total\n'
        "AssertionError: incorrect total"
    )
    assert repro_touches_suspects(output, suspects) is True


def test_repro_does_not_touch_suspects_when_traceback_is_unrelated():
    suspects = [
        SimpleNamespace(
            node=SimpleNamespace(file_path="src/cart.py")
        )
    ]
    output = (
        '  File "src/inventory.py", line 42, in update_stock\n'
        "AssertionError: incorrect stock"
    )
    assert repro_touches_suspects(output, suspects) is False

# Reproduction structure validation

def test_structure_accepts_direct_function_import():
    code = """
from app.cart import calculate_total

def test_bug():
    calculate_total(10, 5)
"""
    assert validate_repro_structure(
        code, ["app.cart"], {"calculate_total"}
    ) is None


def test_structure_accepts_aliased_function_import():
    code = """
from app.cart import calculate_total as calc

def test_bug():
    calc(10, 5)
"""
    assert validate_repro_structure(
        code, ["app.cart"], {"calculate_total"}
    ) is None


def test_structure_accepts_aliased_module_import():
    code = """
import app.cart as cart_module

def test_bug():
    cart_module.calculate_total(10, 5)
"""
    assert validate_repro_structure(
        code, ["app.cart"], {"calculate_total"}
    ) is None


def test_structure_accepts_dotted_module_import():
    code = """
import app.cart

def test_bug():
    app.cart.calculate_total(10, 5)
"""
    assert validate_repro_structure(
        code, ["app.cart"], {"calculate_total"}
    ) is None


def test_structure_accepts_star_import_when_suspect_is_called():
    code = """
from app.cart import *

def test_bug():
    calculate_total(10, 5)
"""
    assert validate_repro_structure(
        code, ["app.cart"], {"calculate_total"}
    ) is None


def test_structure_rejects_star_import_without_suspect_call():
    code = """
from app.cart import *

def test_bug():
    print("testing cart")
"""
    result = validate_repro_structure(
        code, ["app.cart"], {"calculate_total"}
    )
    assert result == "the test never calls code from the files under test"


def test_structure_rejects_unconditional_assert_false():
    code = """
def test_bug():
    assert False
"""
    result = validate_repro_structure(
        code, ["app.cart"], {"calculate_total"}
    )
    assert result is not None
    assert "unconditionally" in result


def test_structure_rejects_unconditional_pytest_fail():
    code = """
import pytest

def test_bug():
    pytest.fail("forced failure")
"""
    result = validate_repro_structure(
        code, ["app.cart"], {"calculate_total"}
    )
    assert result is not None
    assert "unconditionally" in result


def test_structure_rejects_unrelated_function_call():
    code = """
from app.helpers import format_currency

def test_bug():
    format_currency(100)
"""
    result = validate_repro_structure(
        code, ["app.cart"], {"calculate_total"}
    )
    assert result == "the test never calls code from the files under test"


def test_structure_rejects_invalid_python_syntax():
    code = """
def test_bug(
    calculate_total(10, 5)
"""
    result = validate_repro_structure(
        code, ["app.cart"], {"calculate_total"}
    )
    assert result is not None
    assert result.startswith("test has a syntax error:")    

# Batch 5 — generate_repro() tests

import asyncio
import json
from types import SimpleNamespace

import pytest

import ai.reasoning.repro_test as repro_module


BUG_REPORT = (
    "The cart total should include tax before returning the final amount."
)

VALID_REPRO_CODE = (
    "from app.cart import calculate_total\n\n"
    "def test_cart_total_includes_tax():\n"
    "    result = calculate_total(100, 10)\n"
    "    assert result == 110\n"
)


def valid_repro_payload():
    return {
        "verdict": "reproducible",
        "report_quote": "cart total should include tax",
        "expectation": "The returned cart total includes the tax amount.",
        "test_code": VALID_REPRO_CODE,
    }


def prepare_generate_repro(monkeypatch):
    suspect = SimpleNamespace(
        node=SimpleNamespace(
            name="calculate_total",
            file_path="app/cart.py",
        )
    )

    monkeypatch.setattr(
        repro_module,
        "select_files",
        lambda suspects: ["app/cart.py"],
    )
    monkeypatch.setattr(
        repro_module,
        "_source_block",
        lambda root, path, suspects: (
            "=== app/cart.py ===\n"
            "def calculate_total(subtotal, tax):\n"
            "    return subtotal\n"
        ),
    )
    monkeypatch.setattr(
        repro_module,
        "existing_tests_context",
        lambda root, suspects: (
            "=== tests/test_cart.py ===\n"
            "def test_existing_cart_contract(): pass"
        ),
    )
    monkeypatch.setattr(
        repro_module,
        "_parse",
        lambda raw: json.loads(raw),
    )

    return [suspect]


def make_llm_response(payload):
    async def llm_call(prompt):
        return json.dumps(payload)

    return llm_call


def test_generate_repro_accepts_valid_response_and_builds_prompt(monkeypatch):
    suspects = prepare_generate_repro(monkeypatch)
    captured = {}

    async def llm_call(prompt):
        captured["prompt"] = prompt
        return json.dumps(valid_repro_payload())

    result = asyncio.run(
        repro_module.generate_repro(
            BUG_REPORT,
            suspects,
            "/fake/repository",
            llm_call,
        )
    )

    assert result.error is None
    assert result.not_reproducible is False
    assert result.code == VALID_REPRO_CODE
    assert result.expectation == (
        "The returned cart total includes the tax amount."
    )
    assert result.quote == "cart total should include tax"

    prompt = captured["prompt"]
    assert BUG_REPORT in prompt
    assert "app/cart.py" in prompt
    assert "import as: app.cart" in prompt
    assert "calculate_total" in prompt
    assert "tests/test_cart.py" in prompt
    assert "EXISTING TESTS" in prompt


def test_generate_repro_adds_missing_trailing_newline(monkeypatch):
    suspects = prepare_generate_repro(monkeypatch)
    payload = valid_repro_payload()
    payload["test_code"] = VALID_REPRO_CODE.rstrip("\n")

    result = asyncio.run(
        repro_module.generate_repro(
            BUG_REPORT,
            suspects,
            "/fake/repository",
            make_llm_response(payload),
        )
    )

    assert result.error is None
    assert result.code.endswith("\n")
    assert result.code == VALID_REPRO_CODE


def test_generate_repro_handles_not_reproducible_verdict(monkeypatch):
    suspects = prepare_generate_repro(monkeypatch)
    payload = {
        "verdict": "not_reproducible",
        "reason": "The source already returns the expected total.",
    }

    result = asyncio.run(
        repro_module.generate_repro(
            BUG_REPORT,
            suspects,
            "/fake/repository",
            make_llm_response(payload),
        )
    )

    assert result.not_reproducible is True
    assert result.reason == (
        "The source already returns the expected total."
    )
    assert result.error is None
    assert result.code == ""


def test_generate_repro_uses_default_reason_when_missing(monkeypatch):
    suspects = prepare_generate_repro(monkeypatch)
    payload = {"verdict": "not_reproducible", "reason": "  "}

    result = asyncio.run(
        repro_module.generate_repro(
            BUG_REPORT,
            suspects,
            "/fake/repository",
            make_llm_response(payload),
        )
    )

    assert result.not_reproducible is True
    assert result.reason == (
        "the code already behaves as described"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"verdict": ""},
        {"verdict": "unknown"},
    ],
)
def test_generate_repro_rejects_missing_or_invalid_verdict(
    monkeypatch, payload
):
    suspects = prepare_generate_repro(monkeypatch)

    result = asyncio.run(
        repro_module.generate_repro(
            BUG_REPORT,
            suspects,
            "/fake/repository",
            make_llm_response(payload),
        )
    )

    assert result.error is not None
    assert result.error.startswith(
        "invalid or missing reproduction verdict:"
    )
    assert result.code == ""


def test_generate_repro_rejects_invalid_test_code(monkeypatch):
    suspects = prepare_generate_repro(monkeypatch)
    payload = valid_repro_payload()
    payload["test_code"] = "def test_broken(:\n    pass"

    result = asyncio.run(
        repro_module.generate_repro(
            BUG_REPORT,
            suspects,
            "/fake/repository",
            make_llm_response(payload),
        )
    )

    assert result.error is not None
    assert "syntax error" in result.error
    assert result.code == ""


@pytest.mark.parametrize(
    "quote",
    [
        "",
        "The system should always calculate a discount.",
    ],
)
def test_generate_repro_rejects_missing_or_ungrounded_quote(
    monkeypatch, quote
):
    suspects = prepare_generate_repro(monkeypatch)
    payload = valid_repro_payload()
    payload["report_quote"] = quote

    result = asyncio.run(
        repro_module.generate_repro(
            BUG_REPORT,
            suspects,
            "/fake/repository",
            make_llm_response(payload),
        )
    )

    assert result.error is not None
    assert "not grounded in the bug report" in result.error
    assert result.code == ""


def test_generate_repro_rejects_missing_expectation(monkeypatch):
    suspects = prepare_generate_repro(monkeypatch)
    payload = valid_repro_payload()
    payload["expectation"] = "  "

    result = asyncio.run(
        repro_module.generate_repro(
            BUG_REPORT,
            suspects,
            "/fake/repository",
            make_llm_response(payload),
        )
    )

    assert result.error == (
        "the model did not explain the expected behaviour"
    )
    assert result.code == ""


def test_generate_repro_converts_ordinary_exception_to_error(monkeypatch):
    suspects = prepare_generate_repro(monkeypatch)

    async def failing_llm(prompt):
        raise RuntimeError("model call failed")

    result = asyncio.run(
        repro_module.generate_repro(
            BUG_REPORT,
            suspects,
            "/fake/repository",
            failing_llm,
        )
    )

    assert result.error == "RuntimeError: model call failed"
    assert result.not_reproducible is False
    assert result.code == ""


def test_generate_repro_reraises_all_families_failed_error(monkeypatch):
    suspects = prepare_generate_repro(monkeypatch)

    failure = repro_module.AllFamiliesFailedError.__new__(
        repro_module.AllFamiliesFailedError
    )
    BaseException.__init__(failure, "all model families failed")

    async def failing_llm(prompt):
        raise failure

    with pytest.raises(repro_module.AllFamiliesFailedError):
        asyncio.run(
            repro_module.generate_repro(
                BUG_REPORT,
                suspects,
                "/fake/repository",
                failing_llm,
            )
        )

# Batch 6 — existing_tests_context() and _module_name() tests

from pathlib import Path
from types import SimpleNamespace

import ai.reasoning.repro_test as repro_module


def make_context_suspect(
    name="calculate_total",
    file_path="app/cart.py",
):
    return SimpleNamespace(
        node=SimpleNamespace(
            name=name,
            file_path=file_path,
        )
    )


def write_test_file(root, relative_path, content):
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def test_module_name_converts_nested_python_path():
    assert repro_module._module_name("src/services/cart.py") == (
        "src.services.cart"
    )


def test_module_name_normalizes_windows_path():
    assert repro_module._module_name(
        r"src\services\cart.py"
    ) == "src.services.cart"


def test_module_name_removes_init_module_component():
    assert repro_module._module_name(
        "app/cart/__init__.py"
    ) == "app.cart"


def test_existing_tests_context_finds_suspect_function(tmp_path):
    write_test_file(
        tmp_path,
        "tests/test_cart.py",
        "def test_total():\n"
        "    calculate_total(100, 10)\n",
    )

    result = repro_module.existing_tests_context(
        str(tmp_path),
        [make_context_suspect()],
    )

    assert "tests/test_cart.py" in result
    assert "calculate_total" in result


def test_existing_tests_context_finds_suspect_module_name(tmp_path):
    write_test_file(
        tmp_path,
        "tests/test_cart.py",
        "from app import cart\n"
        "def test_cart_module():\n"
        "    assert cart is not None\n",
    )

    result = repro_module.existing_tests_context(
        str(tmp_path),
        [make_context_suspect(name="calculate_total")],
    )

    assert "tests/test_cart.py" in result
    assert "from app import cart" in result


def test_existing_tests_context_excludes_generated_repro_file(tmp_path):
    write_test_file(
        tmp_path,
        "tests/test_cart.py",
        "def test_cart():\n"
        "    calculate_total(100, 10)\n",
    )
    write_test_file(
        tmp_path,
        repro_module.REPRO_FILE,
        "def test_generated():\n"
        "    calculate_total(100, 10)\n",
    )

    result = repro_module.existing_tests_context(
        str(tmp_path),
        [make_context_suspect()],
    )

    assert "tests/test_cart.py" in result
    assert repro_module.REPRO_FILE not in result


def test_existing_tests_context_ignores_virtualenv_and_cache_directories(
    tmp_path,
):
    write_test_file(
        tmp_path,
        "tests/test_cart.py",
        "calculate_total(100, 10)\n",
    )
    write_test_file(
        tmp_path,
        ".venv/tests/test_venv.py",
        "calculate_total(100, 10)\n",
    )
    write_test_file(
        tmp_path,
        "node_modules/tests/test_node.py",
        "calculate_total(100, 10)\n",
    )
    write_test_file(
        tmp_path,
        "__pycache__/tests/test_cache.py",
        "calculate_total(100, 10)\n",
    )

    result = repro_module.existing_tests_context(
        str(tmp_path),
        [make_context_suspect()],
    )

    assert "tests/test_cart.py" in result
    assert "test_venv.py" not in result
    assert "test_node.py" not in result
    assert "test_cache.py" not in result


def test_existing_tests_context_limits_number_of_files(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(repro_module, "MAX_TEST_FILES", 3)

    for i in range(5):
        write_test_file(
            tmp_path,
            f"tests/test_cart_{i}.py",
            f"# calculate_total reference {i}\n",
        )

    result = repro_module.existing_tests_context(
        str(tmp_path),
        [make_context_suspect()],
    )

    included_files = [
        line for line in result.splitlines()
        if line.startswith("=== ") and line.endswith(" ===")
    ]

    assert len(included_files) == 3


def test_existing_tests_context_limits_source_text_size(tmp_path):
    content = (
        "calculate_total\n"
        + ("x" * (repro_module.MAX_TEST_CONTEXT_CHARS + 1000))
    )
    write_test_file(
        tmp_path,
        "tests/test_cart.py",
        content,
    )

    result = repro_module.existing_tests_context(
        str(tmp_path),
        [make_context_suspect()],
    )

    # The implementation budgets source text, excluding the file header.
    header = "=== tests/test_cart.py ===\n"
    assert result.startswith(header)
    assert len(result[len(header):]) == (
        repro_module.MAX_TEST_CONTEXT_CHARS
    )


def test_existing_tests_context_skips_unreadable_test_files(
    tmp_path, monkeypatch
):
    readable = write_test_file(
        tmp_path,
        "tests/test_cart_readable.py",
        "calculate_total(100, 10)\n",
    )
    unreadable = write_test_file(
        tmp_path,
        "tests/test_cart_unreadable.py",
        "calculate_total(200, 20)\n",
    )

    original_read_text = Path.read_text

    def controlled_read_text(path, *args, **kwargs):
        if path == unreadable:
            raise OSError("permission denied")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", controlled_read_text)

    result = repro_module.existing_tests_context(
        str(tmp_path),
        [make_context_suspect()],
    )

    assert "tests/test_cart_readable.py" in result
    assert "tests/test_cart_unreadable.py" not in result

# Batch 7 — traceback classification edge cases

from types import SimpleNamespace

import ai.reasoning.repro_test as repro_module


def make_repro_run(output, exit_code=1, timed_out=False):
    return SimpleNamespace(
        timed_out=timed_out,
        exit_code=exit_code,
        output=output,
    )


def make_suspect_path(path, name="calculate_total"):
    return SimpleNamespace(
        node=SimpleNamespace(
            name=name,
            file_path=path,
        )
    )


def test_repro_touches_suspects_accepts_second_matching_suspect():
    suspects = [
        make_suspect_path("src/inventory.py", "update_stock"),
        make_suspect_path("src/cart.py", "calculate_total"),
        make_suspect_path("src/payment.py", "charge"),
    ]
    output = (
        "test_miniai_repro.py:8: in test_bug\n"
        "src/cart.py:42: in calculate_total\n"
    )

    assert repro_module.repro_touches_suspects(output, suspects) is True


def test_repro_touches_suspects_rejects_unrelated_application_frame():
    suspects = [
        make_suspect_path("src/cart.py"),
    ]
    output = (
        "test_miniai_repro.py:8: in test_bug\n"
        "src/auth.py:42: in authenticate\n"
    )

    assert repro_module.repro_touches_suspects(output, suspects) is False


def test_repro_touches_suspects_allows_assertion_without_app_frame():
    suspects = [make_suspect_path("src/cart.py")]
    output = (
        "test_miniai_repro.py:8: in test_bug\n"
        "E   AssertionError: expected 110, got 100\n"
    )

    assert repro_module.repro_touches_suspects(output, suspects) is True


def test_repro_touches_suspects_ignores_library_frames():
    suspects = [make_suspect_path("src/cart.py")]
    output = (
        "test_miniai_repro.py:8: in test_bug\n"
        "C:\\Python\\Lib\\site-packages\\package\\module.py:42: in run\n"
    )

    assert repro_module.repro_touches_suspects(output, suspects) is True


def test_repro_touches_suspects_normalizes_windows_repository_path():
    suspects = [make_suspect_path(r"src\cart.py")]
    output = (
        "test_miniai_repro.py:8: in test_bug\n"
        r"src\cart.py:42: in calculate_total"
        "\n"
    )

    assert repro_module.repro_touches_suspects(output, suspects) is True


def test_classify_repro_run_accepts_generated_test_assertion_failure():
    output = (
        "FAILED test_miniai_repro.py::test_bug - AssertionError\n"
        "test_miniai_repro.py:8: in test_bug\n"
        "E   AssertionError: expected 110, got 100\n"
    )

    accepted, reason = repro_module.classify_repro_run(
        make_repro_run(output)
    )

    assert accepted is True
    assert reason == "reproduced"


def test_classify_repro_run_rejects_same_named_test_in_subdirectory():
    output = (
        "FAILED tests/test_miniai_repro.py::test_bug - AssertionError\n"
        "test_miniai_repro.py:8: in test_bug\n"
        "E   AssertionError: expected 110, got 100\n"
    )

    accepted, reason = repro_module.classify_repro_run(
        make_repro_run(output)
    )

    assert accepted is False
    assert reason == (
        "a different test failed, not the generated reproduction test"
    )


def test_classify_repro_run_rejects_other_test_failure():
    output = (
        "FAILED tests/test_cart.py::test_existing - AssertionError\n"
        "tests/test_cart.py:12: in test_existing\n"
        "E   AssertionError: old contract broken\n"
    )

    accepted, reason = repro_module.classify_repro_run(
        make_repro_run(output)
    )

    assert accepted is False
    assert reason == (
        "a different test failed, not the generated reproduction test"
    )


def test_classify_repro_run_rejects_generated_test_import_failure():
    output = (
        "FAILED test_miniai_repro.py::test_bug - ModuleNotFoundError\n"
        "test_miniai_repro.py:2: in test_bug\n"
        "E   ModuleNotFoundError: No module named 'missing_package'\n"
    )

    accepted, reason = repro_module.classify_repro_run(
        make_repro_run(output)
    )

    assert accepted is False
    assert reason == (
        "the reproduction test is broken (import/name/syntax error), "
        "not a real failure"
    )


def test_classify_repro_run_accepts_exception_raised_in_repository_code():
    output = (
        "FAILED test_miniai_repro.py::test_bug - NameError\n"
        "test_miniai_repro.py:8: in test_bug\n"
        "src/cart.py:42: in calculate_total\n"
        "E   NameError: name 'tax_amount' is not defined\n"
    )

    accepted, reason = repro_module.classify_repro_run(
        make_repro_run(output)
    )

    assert accepted is True
    assert reason == "reproduced (the code raised an exception)"

# Batch 8 — security and robustness tests

def test_check_repro_code_rejects_aliased_blocked_import():
    code = (
        "import subprocess as sp\n"
        "def test_bug():\n"
        "    sp.run(['echo', 'hello'])\n"
    )

    result = repro_module.check_repro_code(code)

    assert result is not None
    assert "blocked module" in result


def test_check_repro_code_rejects_from_blocked_module():
    code = (
        "from socket import socket as make_socket\n"
        "def test_bug():\n"
        "    make_socket()\n"
    )

    result = repro_module.check_repro_code(code)

    assert result is not None
    assert "blocked module" in result


def test_check_repro_code_rejects_nested_blocked_import():
    code = (
        "def test_bug():\n"
        "    if True:\n"
        "        import requests\n"
    )

    result = repro_module.check_repro_code(code)

    assert result is not None
    assert "blocked module" in result


def test_check_repro_code_rejects_dynamic_dunder_import():
    code = (
        "def test_bug():\n"
        "    module = __import__('subprocess')\n"
        "    assert module is not None\n"
    )

    result = repro_module.check_repro_code(code)

    assert result is not None
    assert result == "test uses a blocked dynamic import"


def test_check_repro_code_rejects_importlib_dynamic_import():
    code = (
        "import importlib\n"
        "def test_bug():\n"
        "    module = importlib.import_module('socket')\n"
        "    assert module is not None\n"
    )

    result = repro_module.check_repro_code(code)

    assert result is not None
    assert result == "test uses a blocked dynamic import"


def test_check_repro_code_rejects_oversized_test():
    code = (
        "def test_bug():\n"
        "    value = '" + ("x" * repro_module.MAX_TEST_CHARS) + "'\n"
    )

    result = repro_module.check_repro_code(code)

    assert result == "test is too long"


def test_check_repro_code_rejects_invalid_python_syntax():
    code = (
        "def test_bug(:\n"
        "    assert True\n"
    )

    result = repro_module.check_repro_code(code)

    assert result is not None
    assert "syntax error" in result


def test_check_repro_code_rejects_file_without_test_function():
    code = (
        "def calculate_total():\n"
        "    return 100\n"
    )

    result = repro_module.check_repro_code(code)

    assert result == "no test_ function found"


def test_validate_repro_structure_rejects_unrelated_imported_call():
    code = (
        "from unrelated.helpers import helper\n\n"
        "def test_bug():\n"
        "    helper()\n"
    )

    result = repro_module.validate_repro_structure(
        code,
        ["app.cart"],
        {"calculate_total"},
    )

    assert result == "the test never calls code from the files under test"


def test_validate_repro_structure_rejects_test_that_does_not_call_suspect():
    code = (
        "def test_bug():\n"
        "    assert 100 == 100\n"
    )

    result = repro_module.validate_repro_structure(
        code,
        ["app.cart"],
        {"calculate_total"},
    )

    assert result == "the test never calls code from the files under test"

def test_check_repro_code_rejects_aliased_import_module():
    code = """
from importlib import import_module as load_module

def test_generated():
    load_module("subprocess")
"""
    result = check_repro_code(code)

    assert result == "test uses a blocked dynamic import"


def test_check_repro_code_rejects_aliased_builtin_import():
    code = """
from builtins import __import__ as dynamic_import

def test_generated():
    dynamic_import("socket")
"""
    result = check_repro_code(code)

    assert result == "test uses a blocked dynamic import"