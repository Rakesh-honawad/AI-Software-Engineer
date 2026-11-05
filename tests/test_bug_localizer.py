
from graph.schema.node_types import GraphNode, NodeType
from graph.schema.edge_types import GraphEdge, EdgeType

from ai.reasoning.bug_localizer import localize


def make_node(
    node_id,
    name,
    file_path,
    line_range=(1, 50),
    is_deleted=False,
):
    return GraphNode(
        id=node_id,
        type=NodeType.FUNCTION,
        name=name,
        file_path=file_path,
        line_range=line_range,
        is_deleted=is_deleted,
    )


def test_non_positive_top_k_returns_no_suspects():
    nodes = [
        make_node(
            f"node{i}",
            f"calculate_value_{i}",
            f"src/module{i}.py",
        )
        for i in range(3)
    ]

    for top_k in (0, -1, -5):
        result = localize(
            bug_report="calculate_value_0 calculate_value_1 calculate_value_2",
            failure_output="",
            nodes=nodes,
            edges=[],
            top_k=top_k,
        )
        assert result == []


def test_positive_top_k_limits_suspects():
    nodes = [
        make_node(
            f"node{i}",
            f"calculate_value_{i}",
            f"src/module{i}.py",
        )
        for i in range(3)
    ]

    result = localize(
        bug_report="calculate_value_0 calculate_value_1 calculate_value_2",
        failure_output="",
        nodes=nodes,
        edges=[],
        top_k=2,
    )

    assert len(result) <= 2


def test_traceback_selects_matching_function():
    nodes = [
        make_node("inventory", "calculate_total", "src/inventory.py", (10, 30)),
        make_node("shipping", "calculate_shipping", "src/shipping.py", (10, 30)),
    ]

    output = '''
Traceback (most recent call last):
  File "/repo/src/inventory.py", line 20, in calculate_total
AssertionError
'''

    result = localize(
        bug_report="Unexpected calculation failure",
        failure_output=output,
        nodes=nodes,
        edges=[],
    )

    assert result
    assert result[0].node.id == "inventory"


def test_narrowest_line_range_wins():
    nodes = [
        make_node("broad", "calculate_total", "src/inventory.py", (10, 40)),
        make_node("specific", "apply_discount", "src/inventory.py", (20, 30)),
    ]

    output = '''
Traceback (most recent call last):
  File "/repo/src/inventory.py", line 25, in apply_discount
AssertionError
'''

    result = localize(
        bug_report="discount calculation failed",
        failure_output=output,
        nodes=nodes,
        edges=[],
    )

    assert result
    assert result[0].node.id == "specific"


def test_deleted_nodes_are_excluded():
    nodes = [
        make_node(
            "deleted",
            "calculate_total",
            "src/inventory.py",
            is_deleted=True,
        ),
        make_node(
            "active",
            "format_result",
            "src/formatting.py",
        ),
    ]

    result = localize(
        bug_report="calculate_total is incorrect",
        failure_output="",
        nodes=nodes,
        edges=[],
    )

    assert all(s.node.id != "deleted" for s in result)


def test_focus_node_does_not_outrank_traceback_evidence():
    nodes = [
        make_node("actual", "calculate_total", "src/inventory.py", (10, 30)),
        make_node("selected", "format_result", "src/formatting.py"),
    ]

    output = '''
Traceback (most recent call last):
  File "/repo/src/inventory.py", line 20, in calculate_total
AssertionError
'''

    result = localize(
        bug_report="An error occurred",
        failure_output=output,
        nodes=nodes,
        edges=[],
        focus_node_ids=["selected"],
    )

    assert result
    assert result[0].node.id == "actual"


def test_call_graph_expansion_does_not_overpower_traceback():
    nodes = [
        make_node("root", "calculate_total", "src/inventory.py", (10, 30)),
        make_node("child", "format_result", "src/formatting.py"),
    ]

    edges = [
        GraphEdge(
            from_id="root",
            to_id="child",
            type=EdgeType.CALLS,
        )
    ]

    output = '''
Traceback (most recent call last):
  File "/repo/src/inventory.py", line 20, in calculate_total
AssertionError
'''

    result = localize(
        bug_report="An error occurred",
        failure_output=output,
        nodes=nodes,
        edges=edges,
    )

    assert result
    assert result[0].node.id == "root"


def test_imports_and_contains_edges_do_not_expand_localization():
    nodes = [
        make_node("root", "calculate_total", "src/inventory.py", (10, 30)),
        make_node("imported", "format_result", "src/formatting.py"),
        make_node("contained", "validate_result", "src/validation.py"),
    ]

    edges = [
        GraphEdge(
            from_id="root",
            to_id="imported",
            type=EdgeType.IMPORTS,
        ),
        GraphEdge(
            from_id="root",
            to_id="contained",
            type=EdgeType.CONTAINS,
        ),
    ]

    output = '''
Traceback (most recent call last):
  File "/repo/src/inventory.py", line 20, in calculate_total
AssertionError
'''

    result = localize(
        bug_report="An error occurred",
        failure_output=output,
        nodes=nodes,
        edges=edges,
    )

    scores = {s.node.id: s.score for s in result}

    assert "root" in scores
    assert "imported" not in scores
    assert "contained" not in scores


def test_calls_edge_expands_to_callee_not_reverse_callee():
    nodes = [
        make_node("root", "calculate_total", "src/inventory.py", (10, 30)),
        make_node("callee", "format_result", "src/formatting.py"),
        make_node("unrelated", "validate_input", "src/validation.py"),
    ]

    edges = [
        GraphEdge(
            from_id="root",
            to_id="callee",
            type=EdgeType.CALLS,
        )
    ]

    output = '''
Traceback (most recent call last):
  File "/repo/src/inventory.py", line 20, in calculate_total
AssertionError
'''

    result = localize(
        bug_report="An error occurred",
        failure_output=output,
        nodes=nodes,
        edges=edges,
    )

    scores = {s.node.id: s.score for s in result}

    assert "root" in scores
    assert "callee" in scores
    assert "unrelated" not in scores
    assert scores["root"] > scores["callee"]


def test_calls_edge_expands_to_callers():
    nodes = [
        make_node("suspect", "calculate_total", "src/inventory.py", (10, 30)),
        make_node("caller", "checkout", "src/checkout.py"),
        make_node("unrelated", "send_email", "src/email.py"),
    ]

    edges = [
        GraphEdge(
            from_id="caller",
            to_id="suspect",
            type=EdgeType.CALLS,
        )
    ]

    output = '''
Traceback (most recent call last):
  File "/repo/src/inventory.py", line 20, in calculate_total
AssertionError
'''

    result = localize(
        bug_report="An error occurred",
        failure_output=output,
        nodes=nodes,
        edges=edges,
    )

    scores = {s.node.id: s.score for s in result}

    assert "suspect" in scores
    assert "caller" in scores
    assert "unrelated" not in scores
    assert scores["suspect"] > scores["caller"]


def test_two_hop_callee_gets_lower_score_than_direct_callee():
    nodes = [
        make_node("root", "calculate_total", "src/inventory.py", (10, 30)),
        make_node("direct", "apply_discount", "src/discount.py"),
        make_node("indirect", "format_currency", "src/formatting.py"),
    ]

    edges = [
        GraphEdge("root", "direct", EdgeType.CALLS),
        GraphEdge("direct", "indirect", EdgeType.CALLS),
    ]

    output = '''
Traceback (most recent call last):
  File "/repo/src/inventory.py", line 20, in calculate_total
AssertionError
'''

    result = localize(
        bug_report="An error occurred",
        failure_output=output,
        nodes=nodes,
        edges=edges,
    )

    scores = {s.node.id: s.score for s in result}

    assert "root" in scores
    assert "direct" in scores
    assert "indirect" in scores
    assert scores["root"] > scores["direct"] > scores["indirect"]


def test_callee_expansion_stops_after_two_hops():
    nodes = [
        make_node("root", "calculate_total", "src/inventory.py", (10, 30)),
        make_node("direct", "apply_discount", "src/discount.py"),
        make_node("second", "format_currency", "src/formatting.py"),
        make_node("third", "write_invoice", "src/invoice.py"),
    ]

    edges = [
        GraphEdge("root", "direct", EdgeType.CALLS),
        GraphEdge("direct", "second", EdgeType.CALLS),
        GraphEdge("second", "third", EdgeType.CALLS),
    ]

    output = '''
Traceback (most recent call last):
  File "/repo/src/inventory.py", line 20, in calculate_total
AssertionError
'''

    result = localize(
        bug_report="An error occurred",
        failure_output=output,
        nodes=nodes,
        edges=edges,
    )

    scores = {s.node.id: s.score for s in result}

    assert "root" in scores
    assert "direct" in scores
    assert "second" in scores
    assert "third" not in scores


def test_localizer_currently_uses_eligible_edges_regardless_of_confidence():
    nodes = [
        make_node("root", "calculate_total", "src/inventory.py", (10, 30)),
        make_node("child", "apply_discount", "src/discount.py"),
    ]

    edges = [
        GraphEdge(
            from_id="root",
            to_id="child",
            type=EdgeType.CALLS,
            confidence=0.2,
            source=__import__(
                "graph.schema.edge_types",
                fromlist=["EdgeSource"],
            ).EdgeSource.AI,
        )
    ]

    output = '''
Traceback (most recent call last):
  File "/repo/src/inventory.py", line 20, in calculate_total
AssertionError
'''

    result = localize(
        bug_report="An error occurred",
        failure_output=output,
        nodes=nodes,
        edges=edges,
    )

    scores = {s.node.id: s.score for s in result}

    assert "root" in scores
    assert "child" in scores


def test_exact_identifier_ranks_above_similar_names():
    nodes = [
        make_node("exact", "calculate_total", "src/inventory.py"),
        make_node("similar", "calculate_total_shipping", "src/shipping.py"),
    ]

    result = localize(
        bug_report="calculate_total is producing an incorrect value",
        failure_output="",
        nodes=nodes,
        edges=[],
    )

    scores = {s.node.id: s.score for s in result}

    assert "exact" in scores
    assert "similar" in scores
    assert scores["exact"] > scores["similar"]


def test_selected_node_suffix_is_removed_from_bug_report():
    from ai.reasoning.bug_localizer import strip_selected_suffix

    report = "Incorrect checkout calculation (involving cart_total)"

    cleaned = strip_selected_suffix(report)

    assert cleaned == "Incorrect checkout calculation"

def test_selected_node_suffix_preserves_ordinary_bug_report():
    from ai.reasoning.bug_localizer import strip_selected_suffix

    report = "Incorrect checkout calculation"

    assert strip_selected_suffix(report) == report

def test_selected_node_suffix_removal_is_case_insensitive():
    from ai.reasoning.bug_localizer import strip_selected_suffix

    report = "Incorrect checkout calculation (INVOLVING cart_total)"

    assert strip_selected_suffix(report) == "Incorrect checkout calculation"    

def test_empty_bug_report_returns_no_suspects():
    nodes = [
        make_node("n1", "calculate_total", "src/inventory.py"),
        make_node("n2", "process_payment", "src/payment.py"),
    ]

    result = localize(
        bug_report="",
        failure_output="",
        nodes=nodes,
        edges=[],
    )

    assert result == []    

def test_whitespace_only_bug_report_returns_no_suspects():
    nodes = [
        make_node("n1", "calculate_total", "src/inventory.py"),
        make_node("n2", "process_payment", "src/payment.py"),
    ]

    result = localize(
        bug_report="   \n\t   ",
        failure_output="",
        nodes=nodes,
        edges=[],
    )

    assert result == []

def test_traceback_match_ranks_above_lexical_similarity():
    nodes = [
        make_node("trace", "calculate_total", "src/inventory.py"),
        make_node("similar", "calculate_total_shipping", "src/shipping.py"),
    ]

    result = localize(
        bug_report="calculate_total is producing an incorrect value",
        failure_output='File "src/inventory.py", line 10, in calculate_total',
        nodes=nodes,
        edges=[],
    )

    scores = {s.node.id: s.score for s in result}

    assert "trace" in scores
    assert scores["trace"] > scores.get("similar", 0)

def test_application_traceback_is_preferred_over_test_file_traceback():
    nodes = [
        make_node("app", "calculate_total", "src/inventory.py"),
        make_node("test", "test_calculate_total", "tests/test_inventory.py"),
    ]

    result = localize(
        bug_report="checkout calculation failed",
        failure_output=(
            'File "tests/test_inventory.py", line 20\n'
            'File "src/inventory.py", line 10'
        ),
        nodes=nodes,
        edges=[],
    )

    result_ids = {s.node.id for s in result}

    assert "app" in result_ids
    assert "test" not in result_ids


def test_test_node_can_be_returned_when_no_application_candidate_exists():
    nodes = [
        make_node("test", "test_calculate_total", "tests/test_inventory.py"),
    ]

    result = localize(
        bug_report="checkout calculation failed",
        failure_output='File "tests/test_inventory.py", line 20',
        nodes=nodes,
        edges=[],
    )

    assert any(s.node.id == "test" for s in result)    

def test_deleted_test_node_is_excluded():
    nodes = [
        make_node(
            "test",
            "test_calculate_total",
            "tests/test_inventory.py",
            is_deleted=True,
        ),
    ]

    result = localize(
        bug_report="checkout calculation failed",
        failure_output='File "tests/test_inventory.py", line 20',
        nodes=nodes,
        edges=[],
    )

    assert result == []    

def test_duplicate_traceback_frames_do_not_inflate_score():
    nodes = [
        make_node("app", "calculate_total", "src/inventory.py"),
    ]

    single = localize(
        bug_report="checkout calculation failed",
        failure_output='File "src/inventory.py", line 10, in calculate_total',
        nodes=nodes,
        edges=[],
    )

    duplicate = localize(
        bug_report="checkout calculation failed",
        failure_output=(
            'File "src/inventory.py", line 10, in calculate_total\n'
            'File "src/inventory.py", line 10, in calculate_total'
        ),
        nodes=nodes,
        edges=[],
    )

    assert len(single) == 1
    assert len(duplicate) == 1
    assert single[0].score == duplicate[0].score

def test_multiple_traceback_lines_in_same_function_select_one_node():
    nodes = [
        make_node("app", "calculate_total", "src/inventory.py", line_range=(1, 50)),
    ]

    result = localize(
        bug_report="checkout calculation failed",
        failure_output=(
            'File "src/inventory.py", line 10, in calculate_total\n'
            'File "src/inventory.py", line 25, in calculate_total'
        ),
        nodes=nodes,
        edges=[],
    )

    assert len(result) == 1
    assert result[0].node.id == "app"    

def test_traceback_outside_node_line_range_does_not_get_traceback_score():
    nodes = [
        make_node("app", "calculate_total", "src/inventory.py", line_range=(1, 20)),
    ]

    result = localize(
        bug_report="unrelated checkout failure",
        failure_output='File "src/inventory.py", line 80, in calculate_total',
        nodes=nodes,
        edges=[],
    )

    assert len(result) == 1
    assert result[0].node.id == "app"
    assert not any("traceback" in reason.lower() for reason in result[0].reasons)

def test_traceback_does_not_give_evidence_to_same_name_in_wrong_file():
    nodes = [
        make_node("wrong", "calculate_total", "src/shipping.py"),
    ]

    result = localize(
        bug_report="checkout failed",
        failure_output='File "src/inventory.py", line 10, in calculate_total',
        nodes=nodes,
        edges=[],
    )

    assert len(result) == 1
    assert result[0].node.id == "wrong"
    assert not any(
        "traceback" in reason.lower()
        for reason in result[0].reasons
    )

def test_traceback_prefers_matching_file_when_function_names_are_identical():
    nodes = [
        make_node("inventory", "calculate_total", "src/inventory.py"),
        make_node("shipping", "calculate_total", "src/shipping.py"),
    ]

    result = localize(
        bug_report="checkout failed",
        failure_output='File "src/inventory.py", line 10, in calculate_total',
        nodes=nodes,
        edges=[],
    )

    scores = {s.node.id: s.score for s in result}

    assert "inventory" in scores
    assert scores["inventory"] > scores.get("shipping", 0)    

def test_absolute_windows_traceback_path_matches_repository_file():
    nodes = [
        make_node("app", "calculate_total", "src/inventory.py"),
    ]

    result = localize(
        bug_report="checkout failed",
        failure_output=(
            'File "C:\\Users\\Rakesh\\project\\src\\inventory.py", '
            'line 10, in calculate_total'
        ),
        nodes=nodes,
        edges=[],
        repo_root="C:\\Users\\Rakesh\\project",
    )

    assert len(result) == 1
    assert result[0].node.id == "app"
    assert any(
        "stack trace" in reason.lower()
        for reason in result[0].reasons
    )

def test_absolute_linux_traceback_path_matches_repository_file():
    nodes = [
        make_node("app", "calculate_total", "src/inventory.py"),
    ]

    result = localize(
        bug_report="checkout failed",
        failure_output=(
            'File "/home/user/project/src/inventory.py", '
            'line 10, in calculate_total'
        ),
        nodes=nodes,
        edges=[],
        repo_root="/home/user/project",
    )

    assert len(result) == 1
    assert result[0].node.id == "app"
    assert any(
        "stack trace" in reason.lower()
        for reason in result[0].reasons
    )    

def test_relative_traceback_path_works_without_repo_root():
    nodes = [
        make_node("app", "calculate_total", "src/inventory.py"),
    ]

    result = localize(
        bug_report="checkout failed",
        failure_output='File "src/inventory.py", line 10, in calculate_total',
        nodes=nodes,
        edges=[],
    )

    assert len(result) == 1
    assert result[0].node.id == "app"
    assert any(
        "stack trace" in reason.lower()
        for reason in result[0].reasons
    )    

def test_traceback_file_matching_is_case_insensitive():
    nodes = [
        make_node("app", "calculate_total", "src/inventory.py"),
    ]

    result = localize(
        bug_report="checkout failed",
        failure_output='File "SRC/INVENTORY.PY", line 10, in calculate_total',
        nodes=nodes,
        edges=[],
    )

    assert len(result) == 1
    assert result[0].node.id == "app"
    assert any(
        "stack trace" in reason.lower()
        for reason in result[0].reasons
    )    

def test_unknown_traceback_file_does_not_match_unrelated_node():
    nodes = [
        make_node("app", "calculate_total", "src/inventory.py"),
    ]

    result = localize(
        bug_report="unrelated checkout failure",
        failure_output='File "src/unknown.py", line 10, in process_order',
        nodes=nodes,
        edges=[],
    )

    assert result == []    

def test_empty_graph_returns_no_suspects():
    result = localize(
        bug_report="checkout calculation failed",
        failure_output='File "src/inventory.py", line 10',
        nodes=[],
        edges=[],
    )

    assert result == []    

def test_empty_traceback_still_uses_bug_report_evidence():
    nodes = [
        make_node("app", "calculate_total", "src/inventory.py"),
    ]

    result = localize(
        bug_report="calculate_total is producing an incorrect value",
        failure_output="",
        nodes=nodes,
        edges=[],
    )

    assert len(result) == 1
    assert result[0].node.id == "app"
    assert any(
        "mentioned in the bug report" in reason.lower()
        for reason in result[0].reasons
    )

def test_unrelated_bug_report_returns_no_suspects():
    nodes = [
        make_node("inventory", "calculate_total", "src/inventory.py"),
        make_node("payment", "process_payment", "src/payment.py"),
    ]

    result = localize(
        bug_report="The dashboard theme color should be changed",
        failure_output="",
        nodes=nodes,
        edges=[],
    )

    assert result == []

def test_multiple_exact_identifiers_return_multiple_suspects():
    nodes = [
        make_node("inventory", "calculate_total", "src/inventory.py"),
        make_node("payment", "process_payment", "src/payment.py"),
    ]

    result = localize(
        bug_report=(
            "calculate_total returns the wrong amount "
            "before process_payment runs"
        ),
        failure_output="",
        nodes=nodes,
        edges=[],
    )

    result_ids = {s.node.id for s in result}

    assert "inventory" in result_ids
    assert "payment" in result_ids

def test_top_k_limits_multiple_matching_suspects():
    nodes = [
        make_node("inventory", "calculate_total", "src/inventory.py"),
        make_node("payment", "process_payment", "src/payment.py"),
        make_node("order", "create_order", "src/order.py"),
    ]

    result = localize(
        bug_report=(
            "calculate_total process_payment create_order "
            "are all producing incorrect results"
        ),
        failure_output="",
        nodes=nodes,
        edges=[],
        top_k=2,
    )

    assert len(result) == 2    

def test_top_k_keeps_highest_scoring_suspects():
    nodes = [
        make_node("weak", "calculate_total", "src/other.py"),
        make_node("strong", "calculate_total", "src/inventory.py"),
        make_node("medium", "calculate_total", "src/payment.py"),
    ]

    result = localize(
        bug_report="calculate_total is producing an incorrect value",
        failure_output='File "src/inventory.py", line 10, in calculate_total',
        nodes=nodes,
        edges=[],
        top_k=2,
    )

    result_ids = {s.node.id for s in result}

    assert "strong" in result_ids
    assert len(result) == 2    

def test_graph_expansion_excludes_deleted_nodes():
    nodes = [
        make_node("root", "calculate_total", "src/inventory.py"),
        make_node(
            "deleted",
            "apply_discount",
            "src/discount.py",
            is_deleted=True,
        ),
        make_node("active", "validate_order", "src/order.py"),
    ]

    edges = [
        GraphEdge(
            from_id="root",
            to_id="deleted",
            type=EdgeType.CALLS,
        ),
        GraphEdge(
            from_id="root",
            to_id="active",
            type=EdgeType.CALLS,
        ),
    ]

    result = localize(
        bug_report="calculate_total is producing an incorrect value",
        failure_output="",
        nodes=nodes,
        edges=edges,
    )

    result_ids = {s.node.id for s in result}

    assert "deleted" not in result_ids
    assert "active" in result_ids    

def test_duplicate_call_edges_do_not_inflate_suspect_score():
    nodes = [
        make_node("root", "calculate_total", "src/inventory.py"),
        make_node("callee", "apply_discount", "src/discount.py"),
    ]

    single_edge = [
        GraphEdge(
            from_id="root",
            to_id="callee",
            type=EdgeType.CALLS,
        )
    ]

    duplicate_edges = single_edge * 3

    single_result = localize(
        bug_report="calculate_total is producing an incorrect value",
        failure_output='File "src/inventory.py", line 10, in calculate_total',
        nodes=nodes,
        edges=single_edge,
    )

    duplicate_result = localize(
        bug_report="calculate_total is producing an incorrect value",
        failure_output='File "src/inventory.py", line 10, in calculate_total',
        nodes=nodes,
        edges=duplicate_edges,
    )

    single_scores = {s.node.id: s.score for s in single_result}
    duplicate_scores = {s.node.id: s.score for s in duplicate_result}

    assert single_scores == duplicate_scores    

def test_cyclic_call_graph_does_not_duplicate_suspects():
    nodes = [
        make_node("root", "calculate_total", "src/inventory.py"),
        make_node("a", "apply_discount", "src/discount.py"),
        make_node("b", "validate_order", "src/order.py"),
    ]

    edges = [
        GraphEdge(from_id="root", to_id="a", type=EdgeType.CALLS),
        GraphEdge(from_id="a", to_id="b", type=EdgeType.CALLS),
        GraphEdge(from_id="b", to_id="a", type=EdgeType.CALLS),
    ]

    result = localize(
        bug_report="calculate_total is producing an incorrect value",
        failure_output='File "src/inventory.py", line 10, in calculate_total',
        nodes=nodes,
        edges=edges,
    )

    result_ids = [s.node.id for s in result]

    assert "root" in result_ids
    assert len(result_ids) == len(set(result_ids))    

def test_punctuation_only_bug_report_returns_no_suspects():
    nodes = [
        make_node("a", "calculate_total", "src/inventory.py"),
        make_node("b", "apply_discount", "src/discount.py"),
    ]

    result = localize(
        bug_report="!!! ??? ... ---",
        failure_output="",
        nodes=nodes,
        edges=[],
    )

    assert result == []

def test_library_frames_do_not_override_application_frame():
    nodes = [
        make_node("app", "calculate_total", "src/inventory.py"),
        make_node("stdlib", "run", "threading.py"),
    ]

    result = localize(
        bug_report="An error occurred while calculating the total",
        failure_output=(
            'File "C:/Python311/Lib/threading.py", line 100, in run\n'
            'File "src/inventory.py", line 10, in calculate_total'
        ),
        nodes=nodes,
        edges=[],
    )

    result_ids = [s.node.id for s in result]

    assert "app" in result_ids
    assert "stdlib" not in result_ids
    assert result[0].node.id == "app"   

def test_multiple_library_frames_are_excluded():
    nodes = [
        make_node("app", "calculate_total", "src/inventory.py"),
        make_node("thread", "run", "threading.py"),
        make_node("asyncio", "run", "asyncio.py"),
    ]

    result = localize(
        bug_report="An error occurred while calculating the total",
        failure_output=(
            'File "C:/Python311/Lib/asyncio.py", line 200, in run\n'
            'File "C:/Python311/Lib/threading.py", line 100, in run\n'
            'File "src/inventory.py", line 10, in calculate_total'
        ),
        nodes=nodes,
        edges=[],
    )

    result_ids = [s.node.id for s in result]

    assert "app" in result_ids
    assert "thread" not in result_ids
    assert "asyncio" not in result_ids
    assert result[0].node.id == "app"   

def test_library_only_traceback_returns_no_suspects():
    nodes = [
        make_node("thread", "run", "threading.py"),
        make_node("asyncio", "run", "asyncio.py"),
    ]

    result = localize(
        bug_report="",
        failure_output=(
            'File "C:/Python311/Lib/asyncio.py", line 200, in run\n'
            'File "C:/Python311/Lib/threading.py", line 100, in run'
        ),
        nodes=nodes,
        edges=[],
    )

    assert result == []  

def test_explicit_report_identifier_survives_library_frame_filter():
    nodes = [
        make_node("runner", "run", "src/runner.py"),
    ]

    result = localize(
        bug_report="The run function is failing in our application",
        failure_output=(
            'File "C:/Python311/Lib/threading.py", line 100, in run'
        ),
        nodes=nodes,
        edges=[],
    )

    assert any(s.node.id == "runner" for s in result)        

def test_site_packages_frames_are_excluded():
    nodes = [
        make_node("plugin", "run_plugin", "src/plugin.py"),
    ]

    result = localize(
        bug_report="",
        failure_output=(
            'File "C:/project/.venv/Lib/site-packages/pkg/runner.py", '
            'line 20, in run_plugin'
        ),
        nodes=nodes,
        edges=[],
    )

    assert result == []

def test_windows_venv_library_frame_is_excluded():
    nodes = [
        make_node("thread", "run", "threading.py"),
    ]

    result = localize(
        bug_report="",
        failure_output=(
            'File "C:/project/.venv/Lib/threading.py", '
            'line 100, in run'
        ),
        nodes=nodes,
        edges=[],
    )

    assert result == []

def test_project_threading_file_is_not_filtered_as_library():
    nodes = [
        make_node(
            "project",
            "run",
            "threading.py",
            line_range=(1, 50),
        ),
    ]

    result = localize(
        bug_report="",
        failure_output='File "threading.py", line 10, in run',
        nodes=nodes,
        edges=[],
    )

    assert result
    assert result[0].node.id == "project"

def test_linux_standard_library_frame_is_excluded():
    nodes = [
        make_node("stdlib", "run", "threading.py"),
    ]

    result = localize(
        bug_report="",
        failure_output=(
            'File "/usr/lib/python3.11/threading.py", '
            'line 100, in run'
        ),
        nodes=nodes,
        edges=[],
    )

    assert result == []

def test_linux_site_packages_frame_is_excluded():
    nodes = [
        make_node("package", "execute_task", "task.py"),
    ]

    result = localize(
        bug_report="",
        failure_output=(
            'File "/home/user/project/.venv/lib/python3.11/'
            'site-packages/example/task.py", line 20, in execute_task'
        ),
        nodes=nodes,
        edges=[],
    )

    assert result == []

def test_same_function_name_in_library_and_application_prefers_application():
    nodes = [
        make_node("app", "run", "src/worker.py"),
        make_node("stdlib", "run", "threading.py"),
    ]

    result = localize(
        bug_report="",
        failure_output=(
            'File "C:/Python311/Lib/threading.py", line 100, in run\n'
            'File "src/worker.py", line 20, in run'
        ),
        nodes=nodes,
        edges=[],
    )

    result_ids = [s.node.id for s in result]

    assert "app" in result_ids
    assert "stdlib" not in result_ids
    assert result[0].node.id == "app"

def test_project_site_packages_prefix_is_not_filtered():
    nodes = [
        make_node(
            "project",
            "execute_task",
            "src/site-packages_adapter/task.py",
            line_range=(1, 50),
        ),
    ]

    result = localize(
        bug_report="",
        failure_output=(
            'File "src/site-packages_adapter/task.py", '
            'line 10, in execute_task'
        ),
        nodes=nodes,
        edges=[],
    )

    assert result
    assert result[0].node.id == "project"    


def test_duplicate_function_names_in_two_application_files_use_traceback_path():
    nodes = [
        make_node("worker", "run", "src/worker.py"),
        make_node("scheduler", "run", "src/scheduler.py"),
    ]

    result = localize(
        bug_report="",
        failure_output='File "src/worker.py", line 20, in run',
        nodes=nodes,
        edges=[],
    )

    result_ids = {s.node.id for s in result}

    assert "worker" in result_ids
    assert "scheduler" not in result_ids


def test_library_only_frame_does_not_match_project_function_name():
    nodes = [
        make_node("app", "run", "src/worker.py"),
    ]

    result = localize(
        bug_report="",
        failure_output='File "C:/Python311/Lib/threading.py", line 100, in run',
        nodes=nodes,
        edges=[],
    )

    assert result == []


def test_unmatched_application_frame_preserves_function_name_fallback():
    nodes = [
        make_node("worker", "run", "src/worker.py"),
    ]

    result = localize(
        bug_report="",
        failure_output='File "external/worker.py", line 20, in run',
        nodes=nodes,
        edges=[],
    )

    assert len(result) == 1
    assert result[0].node.id == "worker"


def test_unrelated_unmatched_traceback_function_returns_no_suspect():
    nodes = [
        make_node("worker", "process_job", "src/worker.py"),
    ]

    result = localize(
        bug_report="",
        failure_output='File "external/unknown.py", line 20, in unrelated_function',
        nodes=nodes,
        edges=[],
    )

    assert result == []


def test_application_and_library_frames_with_same_name_select_application():
    nodes = [
        make_node("app", "run", "src/worker.py"),
        make_node("stdlib", "run", "threading.py"),
    ]

    result = localize(
        bug_report="",
        failure_output=(
            'File "C:/Python311/Lib/threading.py", line 100, in run\n'
            'File "src/worker.py", line 20, in run'
        ),
        nodes=nodes,
        edges=[],
    )

    result_ids = [s.node.id for s in result]

    assert "app" in result_ids
    assert "stdlib" not in result_ids
    assert result_ids[0] == "app"


def test_two_application_frames_with_same_function_name_select_both_files():
    nodes = [
        make_node("worker", "run", "src/worker.py", line_range=(1, 30)),
        make_node("scheduler", "run", "src/scheduler.py", line_range=(1, 30)),
    ]

    result = localize(
        bug_report="",
        failure_output=(
            'File "src/worker.py", line 10, in run\n'
            'File "src/scheduler.py", line 20, in run'
        ),
        nodes=nodes,
        edges=[],
    )

    result_ids = {s.node.id for s in result}

    assert result_ids == {"worker", "scheduler"}


def test_explicit_bug_report_identifier_survives_traceback_path_filtering():
    nodes = [
        make_node("app", "run", "src/worker.py"),
        make_node("stdlib", "run", "threading.py"),
    ]

    result = localize(
        bug_report="The run function is failing.",
        failure_output='File "C:/Python311/Lib/threading.py", line 100, in run',
        nodes=nodes,
        edges=[],
    )

    result_ids = {s.node.id for s in result}

    assert "app" in result_ids
    assert "stdlib" in result_ids


def test_multiple_library_frames_do_not_create_project_suspects():
    nodes = [
        make_node("worker", "run", "src/worker.py"),
        make_node("handler", "execute", "src/handler.py"),
    ]

    result = localize(
        bug_report="",
        failure_output=(
            'File "C:/Python311/Lib/threading.py", line 100, in run\n'
            'File "C:/Python311/Lib/asyncio/tasks.py", line 200, in execute'
        ),
        nodes=nodes,
        edges=[],
    )

    assert result == []


def test_absolute_windows_application_path_preserves_function_context():
    nodes = [
        make_node("worker", "run", "src/worker.py"),
        make_node("other", "run", "src/other.py"),
    ]

    result = localize(
        bug_report="",
        failure_output='File "C:/projects/demo/src/worker.py", line 20, in run',
        nodes=nodes,
        edges=[],
    )

    result_ids = {s.node.id for s in result}

    assert "worker" in result_ids
    assert "other" not in result_ids


def test_case_insensitive_traceback_path_preserves_function_context():
    nodes = [
        make_node("worker", "run", "src/Worker.py"),
        make_node("other", "run", "src/Other.py"),
    ]

    result = localize(
        bug_report="",
        failure_output='File "SRC/worker.PY", line 20, in run',
        nodes=nodes,
        edges=[],
    )

    result_ids = {s.node.id for s in result}

    assert "worker" in result_ids
    assert "other" not in result_ids
