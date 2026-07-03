"""The safety gate (specs/007-safety-and-permissions.md).

Decides whether a proposed patch may be auto-applied, and renders the standard
planned-action block that is printed before any mutation.
"""

from __future__ import annotations

import json

from pydantic import BaseModel

from sre_agent.config import Mode, Settings
from sre_agent.state import ProposedPatch

# Only these kinds are ever eligible for auto-apply in the lab.
_ALLOWED_KINDS = {"Deployment", "Service"}


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
