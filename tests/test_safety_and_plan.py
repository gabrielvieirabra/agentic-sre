"""Pure unit tests for the safety gate and deterministic plan templates.

No cluster and no LLM required — these guard the safety-critical logic.
"""

from __future__ import annotations

import json

import pytest

from sre_agent.config import Mode, Settings
from sre_agent.graph import PlanLLM, _template_patch, _valid_llm_patch
from sre_agent.safety import check_gate
from sre_agent.state import IncidentCategory, ProposedPatch, RiskLevel


def _settings(mode: Mode) -> Settings:
    return Settings(SRE_MODE=mode.value)


def _good_patch() -> ProposedPatch:
    return ProposedPatch(
        summary="fix", target_kind="Deployment", target_name="web",
        kubectl_patch=json.dumps({"spec": {"template": {}}}),
        rollback="kubectl -n sre-lab rollout undo deploy/web",
        validation="kubectl -n sre-lab rollout status deploy/web",
        risk_level=RiskLevel.LOW,
    )


def test_gate_blocks_in_dry_run():
    d = check_gate(_good_patch(), _settings(Mode.DRY_RUN))
    assert d.allow_apply is False and "dry-run" in d.reason


def test_gate_allows_in_apply_with_complete_patch():
    d = check_gate(_good_patch(), _settings(Mode.APPLY_LOCAL_LAB))
    assert d.allow_apply is True


def test_gate_requires_rollback_and_validation():
    p = _good_patch()
    p.rollback = ""
    assert check_gate(p, _settings(Mode.APPLY_LOCAL_LAB)).allow_apply is False
    p2 = _good_patch()
    p2.validation = ""
    assert check_gate(p2, _settings(Mode.APPLY_LOCAL_LAB)).allow_apply is False


def test_gate_rejects_unknown_kind():
    p = _good_patch()
    p.target_kind = "Secret"
    assert check_gate(p, _settings(Mode.APPLY_LOCAL_LAB)).allow_apply is False


def test_gate_rejects_invalid_json_patch():
    p = _good_patch()
    p.kubectl_patch = "{not json"
    assert check_gate(p, _settings(Mode.APPLY_LOCAL_LAB)).allow_apply is False


@pytest.mark.parametrize(
    "incident,kind",
    [
        (IncidentCategory.IMAGE_PULL, "Deployment"),
        (IncidentCategory.READINESS_PROBE, "Deployment"),
        (IncidentCategory.SERVICE_ENDPOINTS, "Service"),
    ],
)
def test_templates_are_valid_json_and_targeted(incident, kind):
    patch = _template_patch(incident, {})
    assert patch is not None
    assert patch.target_kind == kind and patch.target_name == "web"
    json.loads(patch.kubectl_patch)  # must parse
    assert patch.rollback and patch.validation


def test_template_none_for_unknown_incident():
    assert _template_patch(IncidentCategory.UNKNOWN, {}) is None


def test_valid_llm_patch_scope_check():
    ok = PlanLLM(summary="s", target_kind="Deployment", target_name="web",
                 kubectl_patch=json.dumps({"spec": {"x": 1}}))
    assert _valid_llm_patch(ok) is True
    bad_target = PlanLLM(summary="s", target_kind="Deployment", target_name="other",
                         kubectl_patch=json.dumps({"spec": {}}))
    assert _valid_llm_patch(bad_target) is False
    bad_keys = PlanLLM(summary="s", target_kind="Service", target_name="web",
                       kubectl_patch=json.dumps({"status": {}}))
    assert _valid_llm_patch(bad_keys) is False
