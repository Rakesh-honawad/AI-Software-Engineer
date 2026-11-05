from types import SimpleNamespace

from api.routes.investigate import related_failures


def make_suspects():
    return [
        SimpleNamespace(
            node=SimpleNamespace(
                name="calculate_total",
                file_path="src/inventory.py",
            )
        )
    ]


def test_related_failure_matches_windows_path():
    output = r"""
________ test_inventory ________
C:\repo\src\inventory.py:15: AssertionError
FAILED tests/test_inventory.py::test_inventory
"""
    _, related, unrelated = related_failures(output, make_suspects())

    assert related == ["tests/test_inventory.py::test_inventory"]
    assert unrelated == []


def test_related_failure_matches_forward_slash_path():
    output = """
________ test_inventory ________
/repo/src/inventory.py:15: AssertionError
FAILED tests/test_inventory.py::test_inventory
"""
    _, related, unrelated = related_failures(output, make_suspects())

    assert related == ["tests/test_inventory.py::test_inventory"]
    assert unrelated == []


def test_related_and_unrelated_failures_are_separated():
    output = r"""
________ test_inventory ________
C:\repo\src\inventory.py:15: AssertionError
FAILED tests/test_inventory.py::test_inventory

________ test_shipping ________
C:\repo\src\shipping.py:22: AssertionError
FAILED tests/test_shipping.py::test_shipping
"""
    _, related, unrelated = related_failures(output, make_suspects())

    assert related == ["tests/test_inventory.py::test_inventory"]
    assert unrelated == ["tests/test_shipping.py::test_shipping"]


def test_all_multiple_related_failures_are_retained():
    output = r"""
________ test_inventory_basic ________
C:\repo\src\inventory.py:15: AssertionError
FAILED tests/test_inventory.py::test_inventory_basic

________ test_inventory_discount ________
C:\repo\src\inventory.py:35: AssertionError
FAILED tests/test_inventory.py::test_inventory_discount

________ test_shipping ________
C:\repo\src\shipping.py:22: AssertionError
FAILED tests/test_shipping.py::test_shipping
"""
    _, related, unrelated = related_failures(output, make_suspects())

    assert len(related) == 2
    assert "tests/test_inventory.py::test_inventory_basic" in related
    assert "tests/test_inventory.py::test_inventory_discount" in related
    assert unrelated == ["tests/test_shipping.py::test_shipping"]


def test_related_failure_matches_suspect_symbol():
    output = """
________ test_inventory ________
AssertionError: calculate_total returned the wrong value
FAILED tests/test_inventory.py::test_inventory
"""
    _, related, unrelated = related_failures(output, make_suspects())

    assert related == ["tests/test_inventory.py::test_inventory"]
    assert unrelated == []


def test_similar_but_different_symbol_is_not_matched():
    output = """
________ test_shipping ________
AssertionError: calculate_total_shipping returned the wrong value
FAILED tests/test_shipping.py::test_shipping
"""
    _, related, unrelated = related_failures(output, make_suspects())

    assert related == []
    assert unrelated == ["tests/test_shipping.py::test_shipping"]
