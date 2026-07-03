"""Unit tests for the SQLite memory + Fix Pattern Library (temp db, no cluster)."""

from __future__ import annotations

from sre_agent.memory import Memory
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


def _state(terminal: TerminalState, applied: bool) -> AgentState:
    return AgentState(
        trace_id="t", goal="g", mode="apply-local-lab", scenario="wrong-image-tag",
        incident=IncidentCategory.IMAGE_PULL,
        hypothesis=Hypothesis(root_cause="bad image tag", confidence=0.9),
        proposed_patch=ProposedPatch(summary="set valid image", target_kind="Deployment",
                                     target_name="web", kubectl_patch='{"spec":{}}',
                                     rollback="undo", validation="rollout",
                                     risk_level=RiskLevel.LOW),
        applied_actions=[AppliedAction(description="fix", command="c", applied=applied,
                                       target_kind="Deployment", target_name="web")],
        validation=ValidationResult(healthy=applied),
        terminal_state=terminal, tool_call_count=10, elapsed_seconds=12.3,
    )


def test_record_and_recall(tmp_path):
    mem = Memory(tmp_path / "m.sqlite")
    mem.record_run(_state(TerminalState.FIXED, applied=True))
    past = mem.recall_incidents("image-pull")
    assert len(past) == 1
    assert past[0]["terminal_state"] == "FIXED"
    assert past[0]["fix_summary"] == "set valid image"
    mem.close()


def test_fix_pattern_learns_success_and_failure(tmp_path):
    mem = Memory(tmp_path / "m.sqlite")
    mem.record_run(_state(TerminalState.FIXED, applied=True))
    mem.record_run(_state(TerminalState.FIXED, applied=True))
    mem.record_run(_state(TerminalState.ROLLED_BACK, applied=True))
    best = mem.best_fix_pattern("image-pull", "Deployment")
    assert best is not None
    assert best["successes"] == 2
    assert best["failures"] == 1
    mem.close()


def test_no_pattern_without_success(tmp_path):
    mem = Memory(tmp_path / "m.sqlite")
    mem.record_run(_state(TerminalState.ROLLED_BACK, applied=True))
    assert mem.best_fix_pattern("image-pull", "Deployment") is None
    mem.close()


def test_run_history_orders_recent_first(tmp_path):
    mem = Memory(tmp_path / "m.sqlite")
    s1 = _state(TerminalState.FIXED, applied=True)
    s1.scenario = "first"
    s2 = _state(TerminalState.FIXED, applied=True)
    s2.scenario = "second"
    mem.record_run(s1)
    mem.record_run(s2)
    hist = mem.run_history()
    assert hist[0]["scenario"] == "second"
    mem.close()
