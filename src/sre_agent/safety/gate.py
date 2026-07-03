"""The safety gate (specs/007-safety-and-permissions.md).

Decides whether a proposed patch may be auto-applied, and renders the standard
planned-action block that is printed before any mutation.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from sre_agent.config import Mode, Settings
from sre_agent.state import (
    Mitigation,
    MitigationAction,
    OptimizationAction,
    ProposedPatch,
    Recommendation,
)

# Only these kinds are ever eligible for auto-apply in the lab.
_ALLOWED_KINDS = {"Deployment", "Service"}

# On-call mitigation scope: which lab resources each action may touch.
_ALLOWED_DEPLOYS = {"web", "depsvc"}
_ALLOWED_CONFIGMAPS = {"depsvc-config"}
_DEPLOY_ACTIONS = {MitigationAction.ROLLBACK, MitigationAction.SCALE_OUT, MitigationAction.RESTART}
_CONFIG_ACTIONS = {MitigationAction.CONFIG_PATCH, MitigationAction.DEPENDENCY_FALLBACK}
_MAX_REPLICAS = 5


class GateDecision(BaseModel):
    allow_apply: bool
    reason: str


def planned_action_block(patch: ProposedPatch, settings: Settings) -> str:
    return (
        "Planned action:  {summary}\n"
        "Reason:          {reason}\n"
        "Files/resources: {kind}/{name} in ns={ns} (context={ctx})\n"
        "Rollback:        {rollback}\n"
        "Validation:      {validation}\n"
        "Risk level:      {risk}"
    ).format(
        summary=patch.summary,
        reason=patch.summary,
        kind=patch.target_kind,
        name=patch.target_name,
        ns=settings.namespace,
        ctx=settings.kube_context,
        rollback=patch.rollback or "(none)",
        validation=patch.validation or "(none)",
        risk=patch.risk_level.value,
    )


def check_gate(patch: ProposedPatch | None, settings: Settings) -> GateDecision:
    """Auto-apply is permitted ONLY when every safety condition holds."""
    if patch is None:
        return GateDecision(allow_apply=False, reason="no patch proposed")

    if settings.mode is not Mode.APPLY_LOCAL_LAB:
        return GateDecision(
            allow_apply=False,
            reason=f"mode={settings.mode.value}: mutation not permitted (dry-run/suggest-only)",
        )

    # scope: namespace is hard-locked in Settings; kind + name must be lab resources
    if patch.target_kind not in _ALLOWED_KINDS:
        return GateDecision(allow_apply=False,
                            reason=f"target kind '{patch.target_kind}' not in {_ALLOWED_KINDS}")
    if not patch.target_name:
        return GateDecision(allow_apply=False, reason="target name missing")

    # rollback + validation must be defined
    if not patch.rollback:
        return GateDecision(allow_apply=False, reason="no rollback command defined")
    if not patch.validation:
        return GateDecision(allow_apply=False, reason="no validation command defined")

    # the patch must be valid JSON (we only ever apply structured patches)
    if not patch.kubectl_patch:
        return GateDecision(allow_apply=False, reason="empty patch body")
    try:
        json.loads(patch.kubectl_patch)
    except json.JSONDecodeError as e:
        return GateDecision(allow_apply=False, reason=f"patch is not valid JSON: {e}")

    return GateDecision(allow_apply=True,
                        reason="all safety conditions met (sre-lab, rollback, validation)")


# ---- on-call mitigation gate ----------------------------------------------
def mitigation_action_block(m: Mitigation, settings: Settings) -> str:
    return (
        "Planned action:  [{action}] {summary}\n"
        "Reason:          mitigate the incident (stop the bleeding)\n"
        "Files/resources: {kind}/{name} in ns={ns} (context={ctx}) params={params}\n"
        "Rollback:        {rollback}\n"
        "Validation:      {validation}\n"
        "Risk level:      {risk}"
    ).format(
        action=m.action.value, summary=m.summary, kind=m.target_kind, name=m.target_name,
        ns=settings.namespace, ctx=settings.kube_context, params=m.params or "{}",
        rollback=m.rollback or "(none)", validation=m.validation or "(none)",
        risk=m.risk_level.value,
    )


def check_mitigation_gate(m: Mitigation | None, settings: Settings) -> GateDecision:
    """Auto-apply a mitigation ONLY when every safety condition holds."""
    if m is None:
        return GateDecision(allow_apply=False, reason="no mitigation proposed")
    if settings.mode is not Mode.APPLY_LOCAL_LAB:
        return GateDecision(allow_apply=False,
                            reason=f"mode={settings.mode.value}: mutation not permitted")
    if m.action not in set(MitigationAction):
        return GateDecision(allow_apply=False, reason=f"unknown action '{m.action}'")
    if not m.rollback or not m.validation:
        return GateDecision(allow_apply=False, reason="rollback and validation must be defined")

    # scope: only known lab resources, per action family
    if m.action in _DEPLOY_ACTIONS and (
            m.target_kind != "Deployment" or m.target_name not in _ALLOWED_DEPLOYS):
        return GateDecision(allow_apply=False,
                            reason=f"{m.action} must target a lab Deployment {_ALLOWED_DEPLOYS}")
    if m.action in _CONFIG_ACTIONS and (
            m.target_kind != "ConfigMap" or m.target_name not in _ALLOWED_CONFIGMAPS):
        return GateDecision(allow_apply=False,
                            reason=f"{m.action} must target a lab ConfigMap {_ALLOWED_CONFIGMAPS}")

    # action-specific parameter checks
    if m.action is MitigationAction.SCALE_OUT:
        r = m.params.get("replicas")
        if not isinstance(r, int) or not (1 <= r <= _MAX_REPLICAS):
            return GateDecision(allow_apply=False,
                                reason=f"scale-out replicas must be int in 1..{_MAX_REPLICAS}")
    if m.action in _CONFIG_ACTIONS:
        patch = m.params.get("patch")
        if not patch:
            return GateDecision(allow_apply=False, reason="config mitigation needs a patch body")
        try:
            json.loads(patch)
        except (json.JSONDecodeError, TypeError) as e:
            return GateDecision(allow_apply=False, reason=f"config patch not valid JSON: {e}")

    return GateDecision(allow_apply=True,
                        reason=f"mitigation {m.action} within sre-lab scope")


# ---- efficiency / capacity recommendation gate ----------------------------
_RIGHT_SIZE_ACTIONS = {OptimizationAction.RIGHT_SIZE_DOWN, OptimizationAction.RIGHT_SIZE_UP}


def recommendation_action_block(r: Recommendation, settings: Settings) -> str:
    return (
        "Planned action:  [{action}] {summary}\n"
        "Reason:          efficiency / capacity / cost improvement\n"
        "Files/resources: {kind}/{name} in ns={ns} (context={ctx}) params={params}\n"
        "Rollback:        {rollback}\n"
        "Validation:      {validation}\n"
        "Est. savings:    {savings}\n"
        "Risk level:      {risk}"
    ).format(
        action=r.action.value, summary=r.summary, kind=r.target_kind, name=r.target_name,
        ns=settings.namespace, ctx=settings.kube_context, params=r.params or "{}",
        rollback=r.rollback or "(none)", validation=r.validation or "(none)",
        savings=r.est_savings or "(n/a)", risk=r.risk_level.value,
    )


def check_recommendation_gate(r: Recommendation | None, settings: Settings) -> GateDecision:
    """Auto-apply an efficiency recommendation ONLY when every safety condition holds."""
    if r is None:
        return GateDecision(allow_apply=False, reason="no recommendation proposed")
    if settings.mode is not Mode.APPLY_LOCAL_LAB:
        return GateDecision(allow_apply=False,
                            reason=f"mode={settings.mode.value}: mutation not permitted")
    if r.action not in set(OptimizationAction):
        return GateDecision(allow_apply=False, reason=f"unknown action '{r.action}'")
    if not r.rollback or not r.validation:
        return GateDecision(allow_apply=False, reason="rollback and validation must be defined")

    deploy_actions = _RIGHT_SIZE_ACTIONS | {OptimizationAction.ADJUST_REPLICAS}
    if r.action in deploy_actions and (
            r.target_kind != "Deployment" or r.target_name not in _ALLOWED_DEPLOYS):
        return GateDecision(allow_apply=False,
                            reason=f"{r.action} must target a lab Deployment {_ALLOWED_DEPLOYS}")
    if r.action is OptimizationAction.SET_HPA and (
            r.target_kind != "HorizontalPodAutoscaler" or r.target_name not in _ALLOWED_DEPLOYS):
        return GateDecision(allow_apply=False,
                            reason="SET_HPA must target an HPA named after a lab Deployment")

    if r.action is OptimizationAction.ADJUST_REPLICAS:
        rep = r.params.get("replicas")
        if not isinstance(rep, int) or not (1 <= rep <= _MAX_REPLICAS):
            return GateDecision(allow_apply=False,
                                reason=f"replicas must be int in 1..{_MAX_REPLICAS}")
    if r.action is OptimizationAction.SET_HPA:
        mn, mx, cpu = r.params.get("min"), r.params.get("max"), r.params.get("cpu_percent")
        if not all(isinstance(x, int) for x in (mn, mx, cpu)):
            return GateDecision(allow_apply=False, reason="HPA min/max/cpu_percent must be ints")
        if not (1 <= mn <= mx <= _MAX_REPLICAS) or not (1 <= cpu <= 100):
            return GateDecision(allow_apply=False, reason="HPA bounds invalid")
    if r.action in _RIGHT_SIZE_ACTIONS:
        patch = r.params.get("patch")
        if not patch:
            return GateDecision(allow_apply=False, reason="right-size needs a resources patch body")
        try:
            json.loads(patch)
        except (json.JSONDecodeError, TypeError) as e:
            return GateDecision(allow_apply=False, reason=f"resources patch not valid JSON: {e}")

    return GateDecision(allow_apply=True, reason=f"recommendation {r.action} within sre-lab scope")
