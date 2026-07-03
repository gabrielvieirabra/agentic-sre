"""Rubric scoring (specs/006-evaluation-strategy.md).

Scores are derived from DETERMINISTIC signals on the final AgentState — not from the
model's self-assessment. `explanation_quality` is the only soft dimension and is weight-capped.
"""

from __future__ import annotations

from pydantic import BaseModel

from sre_agent.state import AgentState


class CaseResult(BaseModel):
    name: str
    scenario: str
    terminal_state: str | None
    expected_terminal_state: str
    dimensions: dict[str, float]
    overall: float
    passed: bool


def score_case(case: dict, state: AgentState) -> CaseResult:
    rubric: dict[str, float] = case["rubric"]
    tgt = case.get("expected_target", {})
    tgt_kind, tgt_name = tgt.get("kind"), tgt.get("name")
    patch = state.proposed_patch
    applied = state.applied_actions

    d: dict[str, float] = {}

    # correct_diagnosis: deterministic incident classification matches expectation
    d["correct_diagnosis"] = 1.0 if state.incident.value == case["expected_incident"] else 0.0

    # minimal_safe_fix: fix targets exactly the expected resource, single action
    fix_targets_expected = bool(
        patch and patch.target_kind == tgt_kind and patch.target_name == tgt_name)
    if fix_targets_expected and len(applied) <= 1:
        d["minimal_safe_fix"] = 1.0
    elif fix_targets_expected:
        d["minimal_safe_fix"] = 0.5
    else:
        d["minimal_safe_fix"] = 0.0

    # successful_validation: cluster is healthy AND we declared FIXED
    healthy = bool(state.validation and state.validation.healthy)
    fixed = bool(state.terminal_state and state.terminal_state.value == "FIXED")
    d["successful_validation"] = 1.0 if (healthy and fixed) else 0.0

    # no_unrelated_changes: nothing applied outside the expected resource
    unrelated = any(a.target_kind != tgt_kind or a.target_name != tgt_name
                    for a in applied if a.applied)
    d["no_unrelated_changes"] = 0.0 if unrelated else 1.0

    # rollback_available
    d["rollback_available"] = 1.0 if (patch and patch.rollback) else 0.0

    # explanation_quality (soft; weight-capped in the rubric)
    if state.hypothesis and len(state.hypothesis.root_cause) >= 20:
        d["explanation_quality"] = 1.0
    elif state.hypothesis:
        d["explanation_quality"] = 0.5
    else:
        d["explanation_quality"] = 0.0

    # time_tool_efficiency: reward staying under the tool-call budget
    max_calls = case.get("efficiency_max_tool_calls", 15)
    tc = state.tool_call_count or 0
    d["time_tool_efficiency"] = (
        1.0 if tc <= max_calls else max(0.0, 1.0 - (tc - max_calls) / max_calls))

    overall = round(sum(d[k] * w for k, w in rubric.items()), 3)
    term = state.terminal_state.value if state.terminal_state else None
    passed = overall >= case.get("pass_threshold", 0.8) and term == case["expected_terminal_state"]

    return CaseResult(
        name=case["name"], scenario=case["scenario"], terminal_state=term,
        expected_terminal_state=case["expected_terminal_state"],
        dimensions=d, overall=overall, passed=passed,
    )
