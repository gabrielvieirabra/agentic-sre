"""Unit tests for cost model, capacity math, catalog, and recommendation gate (no cluster/LLM)."""

from __future__ import annotations

import json

import pytest

from sre_agent import cost
from sre_agent.config import Mode, Settings
from sre_agent.optimize import _catalog_recommendation
from sre_agent.safety import check_recommendation_gate
from sre_agent.state import (
    AgentState,
    CapacityPlan,
    EfficiencyIssue,
    OptimizationAction,
    Recommendation,
    ResourceAnalysis,
    RiskLevel,
)


def _settings(mode: Mode = Mode.APPLY_LOCAL_LAB) -> Settings:
    return Settings(SRE_MODE=mode.value)


# ---- cost model -----------------------------------------------------------
@pytest.mark.parametrize("s,expected", [("500m", 500), ("1", 1000), ("250m", 250), (None, 0)])
def test_parse_cpu(s, expected):
    assert cost.parse_cpu_millicores(s) == expected


@pytest.mark.parametrize("s,expected", [("512Mi", 512), ("1Gi", 1024), ("64Mi", 64), (None, 0)])
def test_parse_mem(s, expected):
    assert cost.parse_mem_mib(s) == expected


def test_cost_units_and_savings():
    before = cost.cost_units(500, 256, 2, 0.5)   # 2*(500+128)=1256
    after = cost.cost_units(10, 16, 2, 0.5)       # 2*(10+8)=36
    assert before > after
    assert "->" in cost.savings_str(before, after)


def test_dollarization_optional():
    assert cost.dollars_per_month(500, 256, 2, 0, 0) is None
    assert cost.dollars_per_month(1000, 1024, 1, 0.04, 0.005) > 0


# ---- catalog: issue -> action ---------------------------------------------
def _analysis(**over) -> ResourceAnalysis:
    base = dict(app="web", replicas=2, cpu_usage_m=2, mem_usage_mi=12, cpu_request_m=500,
                mem_request_mi=256, cpu_limit_m=1000, cpu_util_pct=0.2, hpa_present=False,
                cost_units=1256.0)
    base.update(over)
    return ResourceAnalysis(**base)


def _state(issue: EfficiencyIssue, analysis: ResourceAnalysis, **kw) -> AgentState:
    return AgentState(trace_id="t", goal="g", mode="apply-local-lab", scenario="s",
                      target_app="web", efficiency_issue=issue, analysis=analysis, **kw)


def test_catalog_over_provisioned_right_sizes_down():
    r = _catalog_recommendation(_state(EfficiencyIssue.OVER_PROVISIONED, _analysis()), _settings())
    assert r.action is OptimizationAction.RIGHT_SIZE_DOWN
    assert r.target_kind == "Deployment" and json.loads(r.params["patch"])
    assert "->" in r.est_savings


def test_catalog_no_autoscaling_sets_hpa():
    a = _analysis(cpu_request_m=25, cpu_util_pct=8.0)
    r = _catalog_recommendation(_state(EfficiencyIssue.NO_AUTOSCALING, a), _settings())
    assert r.action is OptimizationAction.SET_HPA
    assert r.target_kind == "HorizontalPodAutoscaler"
    assert 1 <= r.params["min"] <= r.params["max"] <= 5


def test_catalog_capacity_risk_adjusts_replicas():
    a = _analysis(cpu_request_m=25, cpu_util_pct=8.0)
    cp = CapacityPlan(peak_multiplier=3, current_replicas=2, required_replicas=3)
    st = _state(EfficiencyIssue.CAPACITY_RISK, a, capacity_plan=cp)
    r = _catalog_recommendation(st, _settings())
    assert r.action is OptimizationAction.ADJUST_REPLICAS and r.params["replicas"] == 3


def test_catalog_efficient_returns_none():
    st = _state(EfficiencyIssue.EFFICIENT, _analysis())
    assert _catalog_recommendation(st, _settings()) is None


# ---- recommendation gate --------------------------------------------------
def _rec(**over) -> Recommendation:
    base = dict(action=OptimizationAction.RIGHT_SIZE_DOWN, summary="s", target_kind="Deployment",
                target_name="web", params={"patch": '{"spec":{}}'}, rollback="restore",
                validation="ready", risk_level=RiskLevel.LOW)
    base.update(over)
    return Recommendation(**base)


def test_gate_blocks_dry_run():
    assert check_recommendation_gate(_rec(), _settings(Mode.DRY_RUN)).allow_apply is False


def test_gate_allows_valid_right_size():
    assert check_recommendation_gate(_rec(), _settings()).allow_apply is True


def test_gate_rejects_unknown_deploy():
    assert check_recommendation_gate(_rec(target_name="kube-dns"), _settings()).allow_apply is False


def test_gate_hpa_bounds():
    good = _rec(action=OptimizationAction.SET_HPA, target_kind="HorizontalPodAutoscaler",
                params={"min": 2, "max": 4, "cpu_percent": 70})
    assert check_recommendation_gate(good, _settings()).allow_apply is True
    bad = good.model_copy(update={"params": {"min": 2, "max": 99, "cpu_percent": 70}})
    assert check_recommendation_gate(bad, _settings()).allow_apply is False


def test_gate_rejects_bad_replicas():
    r = _rec(action=OptimizationAction.ADJUST_REPLICAS, params={"replicas": 42})
    assert check_recommendation_gate(r, _settings()).allow_apply is False
