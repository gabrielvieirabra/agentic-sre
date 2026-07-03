"""Unit tests for on-call mitigation catalog, gate, triage map, and report (no cluster/LLM)."""

from __future__ import annotations

import pytest

from sre_agent.config import Mode, Settings
from sre_agent.oncall import _SIGNAL_MAP, _catalog_mitigation, _followup_items
from sre_agent.reports import build_report
from sre_agent.safety import check_mitigation_gate
from sre_agent.state import (
    AgentState,
    Alert,
    IncidentCategory,
    Mitigation,
    MitigationAction,
    Severity,
    TerminalState,
)


def _settings(mode: Mode) -> Settings:
    return Settings(SRE_MODE=mode.value)


# ---- catalog: incident -> action -----------------------------------------
@pytest.mark.parametrize(
    "incident,action,kind",
    [
        (IncidentCategory.BAD_DEPLOY, MitigationAction.ROLLBACK, "Deployment"),
        (IncidentCategory.OVERLOAD, MitigationAction.SCALE_OUT, "Deployment"),
        (IncidentCategory.CONFIG_DRIFT, MitigationAction.RESTART, "Deployment"),
        (IncidentCategory.DEPENDENCY_DOWN, MitigationAction.DEPENDENCY_FALLBACK, "ConfigMap"),
    ],
)
def test_catalog_maps_incident_to_action(incident, action, kind):
    m = _catalog_mitigation(incident, "web", {})
    assert m is not None and m.action is action and m.target_kind == kind
    assert m.rollback and m.validation  # gate preconditions present


def test_catalog_none_for_non_oncall_incident():
    assert _catalog_mitigation(IncidentCategory.NONE, "web", {}) is None


def test_scale_out_carries_replicas():
    m = _catalog_mitigation(IncidentCategory.OVERLOAD, "web", {})
    assert m.params.get("replicas") == 3


# ---- triage signal map ----------------------------------------------------
def test_signal_map_covers_expected_signals():
    for sig in ("DeployFailed", "HighLatency", "PodsDegraded", "DependencyDown"):
        assert sig in _SIGNAL_MAP
    assert _SIGNAL_MAP["DeployFailed"][0] is IncidentCategory.BAD_DEPLOY


# ---- mitigation gate ------------------------------------------------------
def _scale_mit(**over) -> Mitigation:
    base = dict(action=MitigationAction.SCALE_OUT, summary="scale", target_kind="Deployment",
                target_name="web", params={"replicas": 3}, rollback="scale to 1",
                validation="ready>=3")
    base.update(over)
    return Mitigation(**base)


def test_gate_blocks_in_dry_run():
    d = check_mitigation_gate(_scale_mit(), _settings(Mode.DRY_RUN))
    assert d.allow_apply is False


def test_gate_allows_valid_scale_in_apply():
    d = check_mitigation_gate(_scale_mit(), _settings(Mode.APPLY_LOCAL_LAB))
    assert d.allow_apply is True


def test_gate_rejects_bad_replicas():
    d = check_mitigation_gate(_scale_mit(params={"replicas": 99}), _settings(Mode.APPLY_LOCAL_LAB))
    assert d.allow_apply is False


def test_gate_rejects_unknown_target():
    d = check_mitigation_gate(_scale_mit(target_name="kube-dns"),
                              _settings(Mode.APPLY_LOCAL_LAB))
    assert d.allow_apply is False


def test_gate_requires_rollback_and_validation():
    d = check_mitigation_gate(_scale_mit(rollback=""), _settings(Mode.APPLY_LOCAL_LAB))
    assert d.allow_apply is False


def test_gate_config_action_needs_valid_json_patch():
    good = Mitigation(action=MitigationAction.DEPENDENCY_FALLBACK, summary="fb",
                      target_kind="ConfigMap", target_name="depsvc-config",
                      params={"patch": '{"data":{"mode":"fallback"}}'},
                      rollback="restore", validation="ready")
    assert check_mitigation_gate(good, _settings(Mode.APPLY_LOCAL_LAB)).allow_apply is True
    bad = good.model_copy(update={"params": {"patch": "{not json"}})
    assert check_mitigation_gate(bad, _settings(Mode.APPLY_LOCAL_LAB)).allow_apply is False


# ---- follow-ups + report --------------------------------------------------
def test_followups_per_incident():
    assert _followup_items(IncidentCategory.OVERLOAD)
    assert any("HPA" in i for i in _followup_items(IncidentCategory.OVERLOAD))


def _oncall_nodes(tmp_path):
    from sre_agent.llm import LLM
    from sre_agent.memory import Memory
    from sre_agent.observability import RunLogger
    from sre_agent.oncall import OnCallNodes
    from sre_agent.tools import Tools
    s = _settings(Mode.APPLY_LOCAL_LAB)
    logger = RunLogger(tmp_path, "t", "INFO")
    return OnCallNodes(s, Tools(s, logger), LLM(s), logger, Memory(tmp_path / "m.sqlite"))


def _applied(action: MitigationAction):
    from sre_agent.state import AppliedAction
    return AppliedAction(description="m", command="c", applied=True,
                         target_kind="Deployment", target_name="web",
                         mitigation_action=action.value)


def test_rollback_that_does_not_recover_escalates(tmp_path):
    """A rollback that leaves the service unhealthy must escalate, not auto-undo."""
    from sre_agent.state import ValidationResult
    n = _oncall_nodes(tmp_path)
    state = AgentState(
        trace_id="t", goal="g", mode="apply-local-lab", scenario="bad-deploy-unrecoverable",
        incident=IncidentCategory.BAD_DEPLOY, severity=Severity.SEV2,
        mitigation=_scale_mit(action=MitigationAction.ROLLBACK, params={}),
        applied_actions=[_applied(MitigationAction.ROLLBACK)],
        validation=ValidationResult(healthy=False, ready_replicas=0, desired_replicas=2,
                                    endpoints=0),
    )
    out = n.incident_evaluator(state)
    assert out["terminal_state"] is TerminalState.NEEDS_HUMAN
    assert "escalat" in out["escalation_reason"].lower()


def test_report_renders_oncall_sections():
    state = AgentState(
        trace_id="t", goal="g", mode="apply-local-lab", scenario="overloaded",
        incident=IncidentCategory.OVERLOAD, severity=Severity.SEV3,
        alert=Alert(name="HighLatency-web", signal="HighLatency", severity=Severity.SEV3,
                    source="file"),
        mitigation=_scale_mit(),
        planned_action_block="Planned action:  [scale-out] scale",
        incident_timeline=["🚨 DECLARED SEV3", "🛠️ MITIGATING: [scale-out]", "✅ RESOLVED"],
        followups=["Add an HPA"],
        terminal_state=TerminalState.MITIGATED, eval_score=1.0,
    )
    md = build_report(state, _settings(Mode.APPLY_LOCAL_LAB))
    assert "## Alert (on-call)" in md
    assert "## Mitigation" in md
    assert "## Incident timeline" in md
    assert "## Follow-ups" in md
    assert "MITIGATED" in md
