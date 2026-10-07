"""The grader decides every number we report, so the grader gets tests of its own."""
from datetime import date, datetime
from decimal import Decimal

import pytest

from evals.compare import results_match


def ok(pred, gold, order=False):
    return results_match(pred, gold, order_matters=order)[0]


def test_identical_results_match():
    assert ok([("a", 1), ("b", 2)], [("a", 1), ("b", 2)])


def test_integers_must_be_exact():
    assert not ok([(161542,)], [(161541,)])  # off by one is WRONG
    assert not ok([(8525,)], [(8526,)])


def test_integral_float_equals_int_but_still_exact():
    assert ok([(161541.0,)], [(161541,)])
    assert not ok([(161542.0,)], [(161541,)])


def test_floats_match_within_rounding():
    assert ok([(12.7,)], [(12.74,)])
    assert ok([(65.2,)], [(65.23,)])
    assert not ok([(13.5,)], [(12.74,)])
    assert not ok([(65.0,)], [(70.0,)])


def test_decimal_equals_float():
    assert ok([(Decimal("65.23"),)], [(65.2314,)])


def test_column_names_and_order_are_ignored():
    assert ok([(2, "a")], [("a", 2)])


def test_extra_columns_are_allowed():
    assert ok([("W 20 St & 2 Ave", 9891)], [("W 20 St & 2 Ave",)])


def test_missing_columns_fail():
    assert not ok([("Manhattan",)], [("Manhattan", 115121)])


def test_row_order_ignored_unless_it_matters():
    assert ok([("b", 2), ("a", 1)], [("a", 1), ("b", 2)])
    assert not ok([("b", 2), ("a", 1)], [("a", 1), ("b", 2)], order=True)
    assert ok([("a", 1), ("b", 2)], [("a", 1), ("b", 2)], order=True)


def test_missing_or_extra_rows_fail():
    assert not ok([("a", 1)], [("a", 1), ("b", 2)])
    assert not ok([("a", 1), ("b", 2), ("c", 3)], [("a", 1), ("b", 2)])


def test_right_values_wrong_pairing_fails():
    # Same numbers and same labels, but attached to the wrong labels.
    assert not ok([("member", 22.1), ("casual", 11.8)], [("member", 11.8), ("casual", 22.1)])


def test_text_is_case_insensitive():
    assert ok([("MEMBER",)], [("member",)])


def test_dates_and_midnight_timestamps_are_equal():
    assert ok([(datetime(2025, 6, 24, 0, 0),)], [(date(2025, 6, 24),)])
    assert not ok([(datetime(2025, 6, 24, 13, 0),)], [(date(2025, 6, 24),)])
    assert not ok([(date(2025, 6, 25),)], [(date(2025, 6, 24),)])


def test_null_handling():
    assert ok([(None,)], [(None,)])
    assert not ok([(None,)], [(0,)])


def test_empty_results():
    assert ok([], [])
    assert not ok([(1,)], [])


def test_duplicate_column_values_are_not_double_counted():
    # gold has two columns with identical values; the answer must supply two columns
    assert ok([(5, 5)], [(5, 5)])
    assert not ok([(5,)], [(5, 5)])


@pytest.mark.parametrize("pred", [[("1",)], [("one",)]])
def test_strings_never_equal_numbers(pred):
    assert not ok(pred, [(1,)])
