"""保护评测口径，避免将错误结果计为正确。"""
from scripts.run_eval import compare_rows, is_expected_rejection


def test_row_comparison_preserves_label_value_pairs_and_column_positions():
    expected = [["华东", 100.0], ["华南", 50.0]]
    assert compare_rows([["华南", 50], ["华东", 100.000001]], expected)
    assert not compare_rows([["华东", 50], ["华南", 100]], expected)
    assert not compare_rows([["华东", 100], ["华南", 50, 1]], expected)
    assert not compare_rows([["华东", 100]], expected)


def test_row_comparison_handles_duplicates_and_text():
    assert compare_rows([["甲"], ["甲"]], [["甲"], ["甲"]])
    assert not compare_rows([["乙"]], [["甲"]])
    assert not compare_rows([[True]], [[1]])


def test_unrelated_failure_is_not_counted_as_an_expected_rejection():
    assert is_expected_rejection({"status": "failed", "error_type": "unsupported_question"})
    assert not is_expected_rejection({"status": "failed", "error_type": "timeout"})
    assert not is_expected_rejection({"status": "success", "error_type": "unsupported_question"})


def test_sqlite_quoted_literal_translation_preserves_known_identifiers():
    from scripts.eval_spider_pilot import sqlite_to_duckdb_reference
    sql = sqlite_to_duckdb_reference('SELECT "City" FROM AIRPORTS WHERE "City" = "Alton"', ['City'])
    assert '"City"' in sql
    assert "'Alton'" in sql


def test_spider_preflight_requires_order_when_reference_is_ordered():
    from scripts.eval_spider_pilot import rows_match
    assert rows_match([[2], [1]], [[1], [2]], ordered=False)
    assert not rows_match([[2], [1]], [[1], [2]], ordered=True)


def test_projection_uses_unique_names_and_never_guesses_by_value():
    from scripts.eval_spider_projection import project_named_rows
    query = {"columns": ["name", "Amount", "extra"],
             "data": [{"name": "华东", "Amount": 100, "extra": 200}]}
    assert project_named_rows(query, ["amount", "NAME"]) == ([[100, "华东"]], None)
    assert project_named_rows(query, ["total"])[1] == "missing_or_ambiguous_candidate_column"
    assert project_named_rows({"columns": ["A", "a"], "data": []}, ["a"])[1] == "missing_or_ambiguous_candidate_column"
    assert project_named_rows({"columns": ["a"], "data": []}, ["a"]) == ([], None)
