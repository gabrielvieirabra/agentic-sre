"""Self-tests for rubric scoring with synthetic AgentStates (no cluster/LLM)."""

from __future__ import annotations

from evals.scoring import score_case

from sre_agent.state import (
    AgentState,
    AppliedAction,
    Hypothesis,
    IncidentCategory,
    ProposedPatch,
    RiskLevel,
    TerminalState,
    ValidationResult,
)

CASE = {
    "name": "eval_x", "scenario": "wrong-image-tag",
    "expected_incident": "image-pull",
    "expected_target": {"kind": "Deployment", "name": "web"},
    "expected_terminal_state": "FIXED", "pass_threshold": 0.8,
    "efficiency_max_tool_calls": 15,
    "rubric": {"correct_diagnosis": 0.25, "minimal_safe_fix": 0.20,
               "successful_validation": 0.25, "no_unrelated_changes": 0.10,
               "rollback_available": 0.05, "explanation_quality": 0.10,
               "time_tool_efficiency": 0.05},
}


def _fixed_state() -> AgentState:
    return AgentState(
        trace_id="t", goal="g", mode="apply-local-lab", scenario="wrong-image-tag",
        incident=IncidentCategory.IMAGE_PULL,
        hypothesis=Hypothesis(root_cause="Image tag does not exist -> ImagePullBackOff.",
                              confidence=0.9),
        proposed_patch=ProposedPatch(summary="fix image", target_kind="Deployment",
                                     target_name="web", kubectl_patch="{}",
                                     rollback="rollout undo", validation="rollout status",
                                     risk_level=RiskLevel.LOW),
        applied_actions=[AppliedAction(description="fix", command="c", applied=True,
                                       target_kind="Deployment", target_name="web")],
        validation=ValidationResult(healthy=True, rollout_complete=True, ready_replicas=2,
                                    desired_replicas=2, endpoints=2),
        terminal_state=TerminalState.FIXED, tool_call_count=12,
    )


def test_fixed_case_scores_high_and_passes():
    r = score_case(CASE, _fixed_state())
    assert r.overall >= 0.95 and r.passed is True
    assert r.dimensions["correct_diagnosis"] == 1.0
    assert r.dimensions["successful_validation"] == 1.0


def test_dry_run_proposed_but_not_fixed_does_not_pass():
    s = _fixed_state()
    s.applied_actions = []  # nothing applied (dry-run)
    s.validation = ValidationResult(healthy=False, endpoints=0)
    s.terminal_state = TerminalState.NEEDS_HUMAN
    r = score_case(CASE, s)
    assert r.passed is False
    assert r.dimensions["successful_validation"] == 0.0
    # correct diagnosis + rollback availability still credited
    assert r.dimensions["correct_diagnosis"] == 1.0
    assert r.dimensions["rollback_available"] == 1.0


def test_unrelated_change_penalized():
    s = _fixed_state()
    s.applied_actions.append(
        AppliedAction(description="oops", command="c", applied=True,
                      target_kind="Service", target_name="other"))
    r = score_case(CASE, s)
    assert r.dimensions["no_unrelated_changes"] == 0.0
