"""Unit tests for the Regression Guard comparison logic (no cluster needed)."""

from __future__ import annotations

from evals.runner import regression_guard
from evals.scoring import CaseResult


def _result(name: str, passed: bool) -> CaseResult:
    return CaseResult(
        name=name, scenario="s", terminal_state="FIXED" if passed else "ROLLED_BACK",
        expected_terminal_state="FIXED", dimensions={}, overall=1.0 if passed else 0.3,
        passed=passed,
    )


def test_no_regressions_when_all_still_pass():
    prev = {"a": True, "b": True}
    results = [_result("a", True), _result("b", True)]
    assert regression_guard(prev, results) == []


def test_flags_case_that_passed_before_and_fails_now():
    prev = {"a": True, "b": True}
    results = [_result("a", True), _result("b", False)]
    assert regression_guard(prev, results) == ["b"]


def test_new_case_not_treated_as_regression():
    prev = {"a": True}  # 'b' is brand new
    results = [_result("a", True), _result("b", False)]
    assert regression_guard(prev, results) == []
