"""On-call / incident-response graph: triage an alert, mitigate, communicate, follow up.

A distinct LangGraph capability that reuses the repair loop's observation/evidence/
diagnosis/memory nodes (via subclassing `Nodes`) but swaps the "minimal fix" for a
**mitigation** chosen from a bounded catalog — the on-call job is to stop the bleeding
fast, not to root-cause fix. See specs/010-oncall-incident-response.md.
"""

from __future__ import annotations

import json
import time

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from sre_agent.config import Settings
from sre_agent.graph import Nodes
from sre_agent.llm import LLM
from sre_agent.memory import Memory
from sre_agent.observability import RunLogger
from sre_agent.safety import check_mitigation_gate, mitigation_action_block
from sre_agent.state import (
    AgentState,
    Alert,
    AppliedAction,
    IncidentCategory,
    Mitigation,
    MitigationAction,
    RiskLevel,
    Severity,
    TerminalState,
)
from sre_agent.tools import Tools

# signal -> (incident, default severity)
_SIGNAL_MAP: dict[str, tuple[IncidentCategory, Severity]] = {
    "DeployFailed": (IncidentCategory.BAD_DEPLOY, Severity.SEV2),
    "BadDeploy": (IncidentCategory.BAD_DEPLOY, Severity.SEV2),
    "HighLatency": (IncidentCategory.OVERLOAD, Severity.SEV3),
    "HighTraffic": (IncidentCategory.OVERLOAD, Severity.SEV3),
    "Overload": (IncidentCategory.OVERLOAD, Severity.SEV3),
    "PodsDegraded": (IncidentCategory.CONFIG_DRIFT, Severity.SEV3),
    "ConfigDrift": (IncidentCategory.CONFIG_DRIFT, Severity.SEV3),
    "DependencyDown": (IncidentCategory.DEPENDENCY_DOWN, Severity.SEV2),
    "Upstream5xx": (IncidentCategory.DEPENDENCY_DOWN, Severity.SEV2),
}


class MitigationLLM(BaseModel):
    summary: str
    rationale: str = ""


class OnCallNodes(Nodes):
    """Adds incident-response nodes on top of the repair-loop Nodes."""

    # ---- alert intake -------------------------------------------------
    def alert_ingest(self, state: AgentState) -> dict:
        self.log.set_node("alert_ingest")
        if state.alert is not None:  # provided from a --alert file
            self.log.info("alert from file", signal=state.alert.signal)
            return {}
        # auto-detect: synthesize an alert from cluster signals
        snap = self._snapshot()
        pods = snap.get("pods", [])
        reasons = {p.get("waiting_reason") for p in pods if p.get("waiting_reason")}
        ep = snap.get("endpoints", 0)
        if reasons & {"ImagePullBackOff", "ErrImagePull", "CrashLoopBackOff"}:
            signal = "DeployFailed"
        elif ep == 0 and pods:
            signal = "PodsDegraded"
        elif snap.get("deployment", {}).get("desired", 0) == 1:
            signal = "HighLatency"
        else:
            signal = "None"
        alert = Alert(name=f"auto/{signal}", signal=signal, source="auto",
                      description="synthesized from cluster golden signals",
                      labels={"app": "web"})
        self.log.info("alert synthesized", signal=signal)
        return {"alert": alert}

    def alert_triage(self, state: AgentState) -> dict:
        self.log.set_node("alert_triage")
        alert = state.alert
        if alert is None:
            return {"incident": IncidentCategory.NONE}
        incident, default_sev = _SIGNAL_MAP.get(
            alert.signal, (IncidentCategory.UNKNOWN, Severity.SEV3))
        # honor an explicit severity from the alert file, else use the mapped default
        severity = alert.severity if alert.source == "file" else default_sev
        alert.severity = severity
        target_app = alert.labels.get("app") or _default_app(incident)
        timeline = [f"🚨 DECLARED {severity.value}: {alert.name} (signal={alert.signal})"]
        self.log.info("triaged", incident=incident.value, severity=severity.value,
                      target_app=target_app)
        return {"incident": incident, "severity": severity, "alert": alert,
                "target_app": target_app, "incident_timeline": timeline}

    # ---- mitigation ---------------------------------------------------
    def mitigation_planner(self, state: AgentState) -> dict:
        self.log.set_node("mitigation_planner")
        label_app = state.alert.labels.get("app") if state.alert else None
        app = label_app or _default_app(state.incident)
        mit = _catalog_mitigation(state.incident, app, state.cluster_snapshot)

        matched = ""
        if mit is not None:
            pat = self.mem.best_fix_pattern(state.incident.value, mit.target_kind)
            if pat:
                matched = (f"Known mitigation for {state.incident.value} "
                           f"({pat['successes']} prior success(es)).")
            # LLM refines the human-readable summary only; the action stays deterministic.
            ctx = {"incident": state.incident.value, "action": mit.action.value,
                   "root_cause": state.hypothesis.root_cause if state.hypothesis else "",
                   "alert": state.alert.model_dump() if state.alert else {}}
            out = self.llm.structured(
                system=("You are a senior SRE on call. Given an incident and a chosen mitigation "
                        "action, write a one-line summary of what you are about to do and why."),
                user=json.dumps(ctx),
                schema=MitigationLLM, logger=self.log,
            )
            if out and out.summary:
                mit.summary = out.summary
        timeline = list(state.incident_timeline)
        detail = state.hypothesis.root_cause if state.hypothesis else state.incident.value
        timeline.append(f"🔎 INVESTIGATING: {detail}")
        self.log.info("mitigation planned", action=mit.action.value if mit else None)
        return {"mitigation": mit, "matched_pattern": matched, "incident_timeline": timeline}

    def mitigation_safety_gate(self, state: AgentState) -> dict:
        self.log.set_node("mitigation_safety_gate")
        m = state.mitigation
        block = mitigation_action_block(m, self.s) if m else "(no mitigation proposed)"
        decision = check_mitigation_gate(m, self.s)
        self.log.write_text("planned_actions.md", block + "\n")
        self.log.info("mitigation gate", allow_apply=decision.allow_apply, reason=decision.reason)
        return {"planned_action_block": block}

    def mitigation_executor(self, state: AgentState) -> dict:
        self.log.set_node("mitigation_executor")
        m = state.mitigation
        decision = check_mitigation_gate(m, self.s)
        if not decision.allow_apply or m is None:
            self.log.info("no mitigation (gated)", reason=decision.reason)
            return {}

        ns = self.s.namespace
        timeline = list(state.incident_timeline)
        timeline.append(f"🛠️ MITIGATING: [{m.action.value}] {m.target_kind}/{m.target_name}")
        action = m.action
        res = None
        act = AppliedAction(description=m.summary, command="", applied=False,
                            target_kind=m.target_kind, target_name=m.target_name,
                            mitigation_action=action.value)

        if action is MitigationAction.ROLLBACK:
            res = self.t.rollout_undo(f"deploy/{m.target_name}")
            act.command = f"kubectl -n {ns} rollout undo deploy/{m.target_name}"
            act.rollback_command = "re-deploy previous good version (manual / escalate)"
            if res.ok:
                self.t.rollout_status(f"deploy/{m.target_name}", 90)

        elif action is MitigationAction.SCALE_OUT:
            prior = state.cluster_snapshot.get("deployment", {}).get("desired", 1) or 1
            replicas = int(m.params.get("replicas", 3))
            res = self.t.scale(f"deploy/{m.target_name}", replicas,
                               rollback=f"scale to {prior}")
            act.command = f"kubectl -n {ns} scale deploy/{m.target_name} --replicas={replicas}"
            act.rollback_command = (
                f"kubectl -n {ns} scale deploy/{m.target_name} --replicas={prior}")
            act.rollback_patch = json.dumps({"replicas": prior})
            if res.ok:
                self.t.rollout_status(f"deploy/{m.target_name}", 90)

        elif action is MitigationAction.RESTART:
            res = self.t.rollout_restart(f"deploy/{m.target_name}")
            act.command = f"kubectl -n {ns} rollout restart deploy/{m.target_name}"
            act.rollback_command = "n/a (restart is safe/idempotent)"
            if res.ok:
                self.t.rollout_status(f"deploy/{m.target_name}", 90)

        else:  # CONFIG_PATCH / DEPENDENCY_FALLBACK
            cm = m.target_name
            cur = self.t.get_json("configmap", cm)
            prior_data = (cur.data or {}).get("data", {}) if cur.ok else {}
            act.rollback_patch = json.dumps({"data": prior_data})
            res = self.t.patch("configmap", cm, m.params.get("patch", ""),
                               rollback="restore previous ConfigMap")
            act.command = f"kubectl -n {ns} patch configmap {cm} -p '{m.params.get('patch', '')}'"
            act.rollback_command = f"kubectl -n {ns} patch configmap {cm} (restore prior data)"
            dep = m.params.get("restart_deploy", "depsvc")
            if res.ok:
                self.t.rollout_restart(f"deploy/{dep}")
                self.t.rollout_status(f"deploy/{dep}", 90)

        act.applied = bool(res and res.ok)
        if act.applied:
            self.log.info("mitigation applied", action=action.value)
        else:
            self.log.error("mitigation failed", error=res.error if res else "no result")
        return {"applied_actions": [act], "incident_timeline": timeline}

    def _mitigation_rollback(self, state: AgentState) -> None:
        for a in state.applied_actions:
            if not a.applied:
                continue
            act = a.mitigation_action
            self.log.warn("rolling back mitigation", action=act, target=a.target_name)
            if act == MitigationAction.SCALE_OUT.value and a.rollback_patch:
                prior = json.loads(a.rollback_patch).get("replicas", 1)
                self.t.scale(f"deploy/{a.target_name}", int(prior))
                self.t.rollout_status(f"deploy/{a.target_name}", 60)
            elif act in (MitigationAction.CONFIG_PATCH.value,
                         MitigationAction.DEPENDENCY_FALLBACK.value) and a.rollback_patch:
                self.t.patch("configmap", a.target_name, a.rollback_patch,
                             rollback="(rollback of a rollback)")
                mp = state.mitigation.params if state.mitigation else {}
                dep = mp.get("restart_deploy", "depsvc")
                self.t.rollout_restart(f"deploy/{dep}")
            # RESTART: nothing to undo. ROLLBACK: cannot auto-undo -> caller escalates.

    def incident_evaluator(self, state: AgentState) -> dict:
        self.log.set_node("incident_evaluator")
        applied = any(a.applied for a in state.applied_actions)
        val = state.validation or state.baseline_validation
        timeline = list(state.incident_timeline)
        terminal: TerminalState
        reason = ""

        if state.incident in (IncidentCategory.NONE,) or state.mitigation is None and not applied \
                and state.alert and state.alert.signal == "None":
            terminal = TerminalState.NO_ACTION_NEEDED
            reason = "no actionable incident detected"
        elif applied and val and val.healthy:
            terminal = TerminalState.MITIGATED
            reason = "mitigation applied; service recovered (root cause -> follow-up)"
        elif applied and (not val or not val.healthy):
            if any(a.mitigation_action == MitigationAction.ROLLBACK.value
                   for a in state.applied_actions if a.applied):
                terminal = TerminalState.NEEDS_HUMAN
                reason = "rollback did not recover the service; escalating (cannot auto-undo)"
            else:
                self._mitigation_rollback(state)
                terminal = TerminalState.ROLLED_BACK
                reason = "mitigation did not recover the service; rolled back"
        elif not applied and state.mitigation is not None:
            terminal = TerminalState.NEEDS_HUMAN
            reason = "dry-run: mitigation proposed but not applied (gate blocks mutation)"
        else:
            terminal = TerminalState.FAILED_SAFELY
            reason = "no safe mitigation could be proposed"

        resolved = terminal is TerminalState.MITIGATED
        prefix = "✅ RESOLVED (MITIGATED): " if resolved else "⚠️ ESCALATED / NOT RESOLVED: "
        timeline.append(prefix + reason)
        score = _oncall_score(state, terminal)
        self.log.info("incident evaluated", terminal=terminal.value, reason=reason)
        return {"terminal_state": terminal, "escalation_reason": reason, "eval_score": score,
                "incident_timeline": timeline, "tool_call_count": self.t.calls,
                "elapsed_seconds": round(time.time() - self._t0, 2)}

    # ---- comms + follow-ups ------------------------------------------
    def incident_comms(self, state: AgentState) -> dict:
        self.log.set_node("incident_comms")
        sev = state.severity.value if state.severity else "SEV?"
        header = [
            f"# Incident channel — {state.trace_id}",
            f"- **Severity:** {sev}",
            f"- **Alert:** {state.alert.name if state.alert else '-'} "
            f"(signal={state.alert.signal if state.alert else '-'})",
            f"- **Incident:** {state.incident.value}",
            f"- **Mitigation:** {state.mitigation.summary if state.mitigation else '-'}",
            f"- **Outcome:** {state.terminal_state.value if state.terminal_state else '?'}",
            "",
            "## Timeline",
        ]
        body = [f"- {line}" for line in state.incident_timeline]
        self.log.write_text("incident_channel.md", "\n".join(header + body) + "\n")
        _notify(self.log, self.s, state)  # local seam; Slack/webhook is off-by-default
        self.log.info("incident comms written", updates=len(state.incident_timeline))
        return {}

    def followups(self, state: AgentState) -> dict:
        self.log.set_node("followups")
        items = _followup_items(state.incident)
        md = [f"# Follow-ups — {state.trace_id}", ""] + [f"- [ ] {i}" for i in items]
        self.log.write_text("followups.md", "\n".join(md) + "\n")
        try:
            self.mem.record_followups(state.trace_id, state.incident.value, items)
        except Exception as e:  # noqa: BLE001 - never break the loop on memory
            self.log.warn("followups memory write failed", error=str(e))
        self.log.info("follow-ups generated", count=len(items))
        return {"followups": items}


# ---- helpers ---------------------------------------------------------------
def _default_app(incident: IncidentCategory) -> str:
    return "depsvc" if incident is IncidentCategory.DEPENDENCY_DOWN else "web"


def _catalog_mitigation(incident: IncidentCategory, app: str, snap: dict) -> Mitigation | None:
    if incident is IncidentCategory.BAD_DEPLOY:
        return Mitigation(
            action=MitigationAction.ROLLBACK,
            summary=f"Roll back Deployment {app} to the previous revision (abort the bad deploy).",
            target_kind="Deployment", target_name=app,
            rollback="re-deploy previous good version (manual / escalate)",
            validation=f"rollout status deploy/{app} + pods Ready + endpoints",
            risk_level=RiskLevel.MEDIUM)
    if incident is IncidentCategory.OVERLOAD:
        return Mitigation(
            action=MitigationAction.SCALE_OUT,
            summary=f"Emergency scale-out {app} to 3 replicas to shed load.",
            target_kind="Deployment", target_name=app, params={"replicas": 3},
            rollback=f"scale deploy/{app} back to prior replicas",
            validation=f"ready replicas >= 3 for deploy/{app} + endpoints",
            risk_level=RiskLevel.LOW)
    if incident is IncidentCategory.CONFIG_DRIFT:
        return Mitigation(
            action=MitigationAction.RESTART,
            summary=f"Rollout restart {app} to pick up current config / clear degraded pods.",
            target_kind="Deployment", target_name=app,
            rollback="n/a (restart is safe/idempotent)",
            validation=f"rollout status deploy/{app} + pods Ready",
            risk_level=RiskLevel.LOW)
    if incident is IncidentCategory.DEPENDENCY_DOWN:
        return Mitigation(
            action=MitigationAction.DEPENDENCY_FALLBACK,
            summary=f"Flip {app} dependency to fallback mode and restart.",
            target_kind="ConfigMap", target_name="depsvc-config",
            params={"patch": json.dumps({"data": {"mode": "fallback"}}), "restart_deploy": app},
            rollback="restore previous ConfigMap mode + restart",
            validation=f"rollout status deploy/{app} + pods Ready",
            risk_level=RiskLevel.MEDIUM)
    return None


def _followup_items(incident: IncidentCategory) -> list[str]:
    return {
        IncidentCategory.BAD_DEPLOY: [
            "Fix forward: correct the bad image/config in the next release",
            "Add a canary stage with automated rollback on failed rollout",
            "Alert when a Deployment stops Progressing",
        ],
        IncidentCategory.OVERLOAD: [
            "Add an HPA (CPU/latency target) so scale-out is automatic",
            "Right-size resource requests/limits",
            "Load-test to find the capacity knee; document it in the runbook",
            "Investigate why replicas were reduced below safe minimum",
        ],
        IncidentCategory.CONFIG_DRIFT: [
            "Add a checksum/config annotation so pods auto-restart on ConfigMap change",
            "Reconcile configuration via GitOps to prevent drift",
        ],
        IncidentCategory.DEPENDENCY_DOWN: [
            "Add a circuit breaker + timeout around the dependency call",
            "Make the fallback path a first-class, tested mode",
            "Add a dependency-health alert with a linked runbook",
        ],
    }.get(incident, ["Write a postmortem and capture action items."])


def _oncall_score(state: AgentState, terminal: TerminalState) -> float:
    if terminal in (TerminalState.MITIGATED, TerminalState.NO_ACTION_NEEDED):
        return 1.0
    if terminal is TerminalState.NEEDS_HUMAN and state.mitigation is not None:
        return 0.6
    if terminal is TerminalState.ROLLED_BACK:
        return 0.3
    return 0.0


def _notify(logger: RunLogger, settings: Settings, state: AgentState) -> None:
    """Local notification seam. Off-by-default hook for Slack/webhook (keeps 'no cloud')."""
    logger.info("notify (local)", channel="#incidents",
                severity=state.severity.value if state.severity else None,
                outcome=state.terminal_state.value if state.terminal_state else None)


# ---- assembly --------------------------------------------------------------
def build_oncall_graph(settings: Settings, tools: Tools, llm: LLM, logger: RunLogger,
                       memory: Memory):
    n = OnCallNodes(settings, tools, llm, logger, memory)
    g = StateGraph(AgentState)

    g.add_node("alert_ingest", n.alert_ingest)
    g.add_node("alert_triage", n.alert_triage)
    g.add_node("cluster_observer", n.cluster_observer)
    g.add_node("evidence_collector", n.evidence_collector)
    g.add_node("hypothesis_generator", n.hypothesis_generator)
    g.add_node("mitigation_planner", n.mitigation_planner)
    g.add_node("mitigation_safety_gate", n.mitigation_safety_gate)
    g.add_node("mitigation_executor", n.mitigation_executor)
    g.add_node("validation_runner", n.validation_runner)
    g.add_node("incident_evaluator", n.incident_evaluator)
    g.add_node("incident_comms", n.incident_comms)
    g.add_node("followups", n.followups)
    g.add_node("memory_writer", n.memory_writer)

    g.add_edge(START, "alert_ingest")
    g.add_edge("alert_ingest", "alert_triage")
    g.add_edge("alert_triage", "cluster_observer")
    g.add_edge("cluster_observer", "evidence_collector")
    g.add_edge("evidence_collector", "hypothesis_generator")
    g.add_edge("hypothesis_generator", "mitigation_planner")
    g.add_edge("mitigation_planner", "mitigation_safety_gate")
    g.add_edge("mitigation_safety_gate", "mitigation_executor")
    g.add_edge("mitigation_executor", "validation_runner")
    g.add_edge("validation_runner", "incident_evaluator")
    g.add_edge("incident_evaluator", "incident_comms")
    g.add_edge("incident_comms", "followups")
    g.add_edge("followups", "memory_writer")
    g.add_edge("memory_writer", END)
    return g.compile()
