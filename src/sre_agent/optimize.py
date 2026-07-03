"""Efficiency / capacity / cost graph: analyze utilization, right-size, tune autoscaling.

A third LangGraph (`sre-agent optimize`) alongside repair/on-call — analysis-first and
low-blast-radius. Deterministic signals (kubectl top + request math) drive recommendations;
the LLM only refines the summary. See specs/011-efficiency-capacity-cost.md.
"""

from __future__ import annotations

import json
import math
import time

from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel

from sre_agent import cost
from sre_agent.config import Settings
from sre_agent.graph import Nodes
from sre_agent.llm import LLM
from sre_agent.memory import Memory
from sre_agent.observability import RunLogger
from sre_agent.safety import check_recommendation_gate, recommendation_action_block
from sre_agent.state import (
    AgentState,
    AppliedAction,
    CapacityPlan,
    EfficiencyIssue,
    OptimizationAction,
    Recommendation,
    ResourceAnalysis,
    RiskLevel,
    TerminalState,
)
from sre_agent.tools import Tools

CPU_FLOOR_M = 10
MEM_FLOOR_MI = 16
LIMIT_RATIO = 2  # limits = requests * ratio
OVERPROV_REQUEST_FLOOR_M = 100  # only call it "over-provisioned" if reserving >= this
OVERPROV_UTIL_PCT = 15
UNDERPROV_UTIL_PCT = 80
TINY_LIMIT_M = 30


class RecommendationLLM(BaseModel):
    summary: str
    rationale: str = ""


class OptimizeNodes(Nodes):
    def _deploy_resources(self, app: str) -> dict:
        res = self.t.get_json("deploy", app)
        if not res.ok or not res.data:
            return {}
        tmpl = res.data.get("spec", {}).get("template", {}).get("spec", {})
        containers = tmpl.get("containers", [])
        return (containers[0].get("resources", {}) if containers else {}) or {}

    def _parse_top(self, app: str) -> tuple[int, int, int]:
        res = self.t.top_pods()
        cpu_m = mem_mi = pods = 0
        if res.ok and res.data:
            for line in res.data.splitlines():
                parts = line.split()
                if len(parts) >= 3 and parts[0].startswith(f"{app}-"):
                    cpu_m += cost.parse_cpu_millicores(parts[1])
                    mem_mi += cost.parse_mem_mib(parts[2])
                    pods += 1
        return cpu_m, mem_mi, pods

    # ---- nodes ------------------------------------------------------
    def utilization_analyzer(self, state: AgentState) -> dict:
        self.log.set_node("utilization_analyzer")
        app = state.target_app
        snap = state.cluster_snapshot
        replicas = snap.get("deployment", {}).get("desired", 0) or 0
        resources = self._deploy_resources(app)
        req = resources.get("requests", {}) or {}
        lim = resources.get("limits", {}) or {}
        cpu_req = cost.parse_cpu_millicores(req.get("cpu"))
        mem_req = cost.parse_mem_mib(req.get("memory"))
        cpu_lim = cost.parse_cpu_millicores(lim.get("cpu"))
        cpu_use, mem_use, _ = self._parse_top(app)

        cpu_total_req = cpu_req * replicas
        mem_total_req = mem_req * replicas
        cpu_util = round(cpu_use / cpu_total_req * 100, 1) if cpu_total_req else 0.0
        mem_util = round(mem_use / mem_total_req * 100, 1) if mem_total_req else 0.0
        hpa_present = self.t.get_hpa(app).ok
        units = cost.cost_units(cpu_req, mem_req, replicas, self.s.mem_cost_weight)

        smells: list[str] = []
        if cpu_req == 0 or mem_req == 0:
            smells.append("no CPU/memory requests set")
        if cpu_req >= OVERPROV_REQUEST_FLOOR_M and cpu_util < OVERPROV_UTIL_PCT:
            smells.append(f"CPU over-provisioned (reserving {cpu_req}m, util {cpu_util}%)")
        if cpu_lim and (cpu_lim <= TINY_LIMIT_M or cpu_util > UNDERPROV_UTIL_PCT):
            smells.append(f"CPU limit may throttle (limit {cpu_lim}m, usage {cpu_use}m)")
        if not hpa_present:
            smells.append("no HorizontalPodAutoscaler")
        if replicas < 2:
            smells.append("single replica (no redundancy)")

        # classify (priority: under > over > no-autoscaling > efficient)
        if cpu_lim and (cpu_lim <= TINY_LIMIT_M or cpu_util > UNDERPROV_UTIL_PCT):
            issue = EfficiencyIssue.UNDER_PROVISIONED
        elif cpu_req >= OVERPROV_REQUEST_FLOOR_M and cpu_util < OVERPROV_UTIL_PCT:
            issue = EfficiencyIssue.OVER_PROVISIONED
        elif not hpa_present:
            issue = EfficiencyIssue.NO_AUTOSCALING
        else:
            issue = EfficiencyIssue.EFFICIENT

        analysis = ResourceAnalysis(
            app=app, replicas=replicas, cpu_usage_m=cpu_use, mem_usage_mi=mem_use,
            cpu_request_m=cpu_req, mem_request_mi=mem_req, cpu_limit_m=cpu_lim,
            cpu_util_pct=cpu_util, mem_util_pct=mem_util, hpa_present=hpa_present,
            cost_units=units, smells=smells,
        )
        self.log.info("analyzed", issue=issue.value, cpu_util=cpu_util, cost_units=units,
                      smells=len(smells))
        return {"analysis": analysis, "efficiency_issue": issue}

    def capacity_planner(self, state: AgentState) -> dict:
        self.log.set_node("capacity_planner")
        a = state.analysis
        peak = state.peak_multiplier
        cur = a.replicas if a else 0
        # headroom floor so idle utilization doesn't trivialize peak planning
        util_frac = max((a.cpu_util_pct / 100.0) if a else 0.0, 0.25)
        required = min(5, max(cur, math.ceil(cur * peak * util_frac / self.s.cpu_target_util)))
        plan = CapacityPlan(
            peak_multiplier=peak, current_replicas=cur, required_replicas=required,
            note=(f"At {peak}x peak, ~{required} replicas keep CPU under "
                  f"{int(self.s.cpu_target_util * 100)}% (from {cur})."),
        )
        issue = state.efficiency_issue
        # capacity risk takes precedence only over "efficient"/"just add HPA"
        if required > cur and issue in (EfficiencyIssue.EFFICIENT, EfficiencyIssue.NO_AUTOSCALING):
            issue = EfficiencyIssue.CAPACITY_RISK
        self.log.info("capacity planned", required=required, current=cur, issue=issue.value)
        return {"capacity_plan": plan, "efficiency_issue": issue}

    def efficiency_planner(self, state: AgentState) -> dict:
        self.log.set_node("efficiency_planner")
        rec = _catalog_recommendation(state, self.s)
        matched = ""
        if rec is not None:
            pat = self.mem.best_fix_pattern(state.efficiency_issue.value, rec.target_kind)
            if pat:
                matched = (f"Known optimization for {state.efficiency_issue.value} "
                           f"({pat['successes']} prior success(es)).")
            ctx = {"issue": state.efficiency_issue.value, "action": rec.action.value,
                   "analysis": state.analysis.model_dump() if state.analysis else {}}
            out = self.llm.structured(
                system=("You are a senior SRE focused on efficiency and capacity. Given an "
                        "efficiency issue and a chosen action, write a one-line summary."),
                user=json.dumps(ctx),
                schema=RecommendationLLM, logger=self.log,
            )
            if out and out.summary:
                rec.summary = out.summary
        self.log.info("recommendation", action=rec.action.value if rec else None)
        return {"recommendation": rec, "matched_pattern": matched}

    def recommendation_gate(self, state: AgentState) -> dict:
        self.log.set_node("recommendation_gate")
        r = state.recommendation
        block = recommendation_action_block(r, self.s) if r else "(no recommendation)"
        decision = check_recommendation_gate(r, self.s)
        self.log.write_text("planned_actions.md", block + "\n")
        self.log.info("recommendation gate", allow_apply=decision.allow_apply,
                      reason=decision.reason)
        return {"planned_action_block": block}

    def recommendation_executor(self, state: AgentState) -> dict:
        self.log.set_node("recommendation_executor")
        r = state.recommendation
        decision = check_recommendation_gate(r, self.s)
        if not decision.allow_apply or r is None:
            self.log.info("no change (gated)", reason=decision.reason)
            return {}

        ns, app = self.s.namespace, r.target_name
        act = AppliedAction(description=r.summary, command="", applied=False,
                            target_kind=r.target_kind, target_name=app,
                            mitigation_action=r.action.value)
        res = None
        if r.action in (OptimizationAction.RIGHT_SIZE_DOWN, OptimizationAction.RIGHT_SIZE_UP):
            act.rollback_patch = json.dumps(_resources_patch(app, self._deploy_resources(app)))
            res = self.t.patch("deploy", app, r.params["patch"], rollback="restore prior resources")
            act.command = f"kubectl -n {ns} patch deploy {app} -p '{r.params['patch']}'"
            act.rollback_command = "restore prior resources"
            if res.ok:
                self.t.rollout_status(f"deploy/{app}", 90)
        elif r.action is OptimizationAction.SET_HPA:
            res = self.t.set_hpa(app, r.params["min"], r.params["max"], r.params["cpu_percent"],
                                 rollback=f"kubectl -n {ns} delete hpa {app}")
            act.command = (f"kubectl -n {ns} autoscale deploy/{app} --min={r.params['min']} "
                           f"--max={r.params['max']} --cpu-percent={r.params['cpu_percent']}")
            act.rollback_command = f"kubectl -n {ns} delete hpa {app}"
        else:  # ADJUST_REPLICAS
            prior = state.analysis.replicas if state.analysis else 1
            reps = int(r.params["replicas"])
            res = self.t.scale(f"deploy/{app}", reps, rollback=f"scale to {prior}")
            act.command = f"kubectl -n {ns} scale deploy/{app} --replicas={reps}"
            act.rollback_command = f"kubectl -n {ns} scale deploy/{app} --replicas={prior}"
            act.rollback_patch = json.dumps({"replicas": prior})
            if res.ok:
                self.t.rollout_status(f"deploy/{app}", 90)

        act.applied = bool(res and res.ok)
        err = "" if act.applied else (res.error if res else "no result")
        self.log.info("applied" if act.applied else "apply failed",
                      action=r.action.value, error=err)
        return {"applied_actions": [act]}

    def _optimize_rollback(self, state: AgentState) -> None:
        for a in state.applied_actions:
            if not a.applied:
                continue
            self.log.warn("rolling back optimization", action=a.mitigation_action)
            if a.mitigation_action in (OptimizationAction.RIGHT_SIZE_DOWN.value,
                                       OptimizationAction.RIGHT_SIZE_UP.value) and a.rollback_patch:
                self.t.patch("deploy", a.target_name, a.rollback_patch, rollback="(undo)")
                self.t.rollout_status(f"deploy/{a.target_name}", 60)
            elif (a.mitigation_action == OptimizationAction.ADJUST_REPLICAS.value
                  and a.rollback_patch):
                prior = int(json.loads(a.rollback_patch)["replicas"])
                self.t.scale(f"deploy/{a.target_name}", prior)
            elif a.mitigation_action == OptimizationAction.SET_HPA.value:
                self.t.delete_hpa(a.target_name)

    def efficiency_evaluator(self, state: AgentState) -> dict:
        self.log.set_node("efficiency_evaluator")
        applied = any(a.applied for a in state.applied_actions)
        val = state.validation or state.baseline_validation
        if state.efficiency_issue is EfficiencyIssue.EFFICIENT or state.recommendation is None:
            terminal, reason = TerminalState.NO_ACTION_NEEDED, "workload already efficient"
        elif applied and val and val.healthy:
            terminal, reason = TerminalState.IMPROVED, "optimization applied; workload healthy"
        elif applied and (not val or not val.healthy):
            self._optimize_rollback(state)
            terminal, reason = TerminalState.ROLLED_BACK, "optimization left workload unhealthy"
        elif not applied and state.recommendation is not None:
            terminal, reason = TerminalState.NEEDS_HUMAN, "dry-run: proposed, not applied"
        else:
            terminal, reason = TerminalState.FAILED_SAFELY, "no safe optimization"
        score = 1.0 if terminal in (TerminalState.IMPROVED, TerminalState.NO_ACTION_NEEDED) \
            else (0.6 if terminal is TerminalState.NEEDS_HUMAN and state.recommendation else
                  (0.3 if terminal is TerminalState.ROLLED_BACK else 0.0))
        self.log.info("evaluated", terminal=terminal.value, reason=reason)
        return {"terminal_state": terminal, "escalation_reason": reason, "eval_score": score,
                "tool_call_count": self.t.calls,
                "elapsed_seconds": round(time.time() - self._t0, 2)}

    def efficiency_followups(self, state: AgentState) -> dict:
        self.log.set_node("efficiency_followups")
        items = _followup_items(state.efficiency_issue)
        md = [f"# Follow-ups — {state.trace_id}", ""] + [f"- [ ] {i}" for i in items]
        self.log.write_text("followups.md", "\n".join(md) + "\n")
        issue_key = state.efficiency_issue.value if state.efficiency_issue else "efficiency"
        try:
            self.mem.record_followups(state.trace_id, issue_key, items)
        except Exception as e:  # noqa: BLE001
            self.log.warn("followups memory write failed", error=str(e))
        return {"followups": items}


# ---- catalog + helpers -----------------------------------------------------
def _resources_patch(app: str, resources: dict) -> dict:
    return {"spec": {"template": {"spec": {"containers": [
        {"name": app, "resources": resources}]}}}}


def _catalog_recommendation(state: AgentState, s: Settings) -> Recommendation | None:
    a = state.analysis
    app = state.target_app
    issue = state.efficiency_issue
    if a is None or issue is EfficiencyIssue.EFFICIENT:
        return None

    if issue is EfficiencyIssue.OVER_PROVISIONED:
        new_cpu = max(CPU_FLOOR_M, a.cpu_usage_m * 2)
        new_mem = max(MEM_FLOOR_MI, a.mem_usage_mi * 2)
        resources = {"requests": {"cpu": f"{new_cpu}m", "memory": f"{new_mem}Mi"},
                     "limits": {"cpu": f"{new_cpu * LIMIT_RATIO}m",
                                "memory": f"{new_mem * LIMIT_RATIO}Mi"}}
        before = a.cost_units
        after = cost.cost_units(new_cpu, new_mem, a.replicas, s.mem_cost_weight)
        return Recommendation(
            action=OptimizationAction.RIGHT_SIZE_DOWN,
            summary=f"Right-size {app} requests to ~usage ({new_cpu}m/{new_mem}Mi) to cut waste.",
            target_kind="Deployment", target_name=app,
            params={"patch": json.dumps(_resources_patch(app, resources))},
            rollback="restore prior resources",
            validation=f"pods Ready + cost-units drop for {app}",
            risk_level=RiskLevel.LOW, est_savings=cost.savings_str(before, after))
    if issue is EfficiencyIssue.UNDER_PROVISIONED:
        new_lim = max(100, a.cpu_usage_m * 3, (a.cpu_limit_m or 0) * 4)
        resources = {"limits": {"cpu": f"{new_lim}m"}}
        return Recommendation(
            action=OptimizationAction.RIGHT_SIZE_UP,
            summary=f"Raise {app} CPU limit to {new_lim}m to stop throttling.",
            target_kind="Deployment", target_name=app,
            params={"patch": json.dumps(_resources_patch(app, resources))},
            rollback="restore prior resources", validation=f"pods Ready, no throttling for {app}",
            risk_level=RiskLevel.LOW, est_savings="(perf; small cost increase)")
    if issue is EfficiencyIssue.NO_AUTOSCALING:
        mn = max(2, a.replicas)
        mx = min(5, max(mn, math.ceil(state.peak_multiplier * max(a.replicas, 1))))
        cpu_pct = int(s.cpu_target_util * 100)
        return Recommendation(
            action=OptimizationAction.SET_HPA,
            summary=f"Add an HPA for {app} (min={mn} max={mx} cpu={cpu_pct}%) for autoscaling.",
            target_kind="HorizontalPodAutoscaler", target_name=app,
            params={"min": mn, "max": mx, "cpu_percent": cpu_pct},
            rollback=f"delete hpa {app}", validation=f"hpa/{app} exists (autoscaling/v2)",
            risk_level=RiskLevel.LOW, est_savings="(capacity headroom; scales with load)")
    if issue is EfficiencyIssue.CAPACITY_RISK:
        req = state.capacity_plan.required_replicas if state.capacity_plan else a.replicas
        return Recommendation(
            action=OptimizationAction.ADJUST_REPLICAS,
            summary=f"Scale {app} to {req} replicas to cover the {state.peak_multiplier}x peak.",
            target_kind="Deployment", target_name=app, params={"replicas": req},
            rollback=f"scale {app} to {a.replicas}",
            validation=f"ready replicas >= {req} for {app}",
            risk_level=RiskLevel.LOW, est_savings="(capacity; cost scales with replicas)")
    return None


def _followup_items(issue: EfficiencyIssue | None) -> list[str]:
    return {
        EfficiencyIssue.OVER_PROVISIONED: [
            "Set resource requests from observed p95 usage (VPA recommender)",
            "Add a cost/utilization dashboard + budget alert",
        ],
        EfficiencyIssue.UNDER_PROVISIONED: [
            "Load-test to size CPU limits against p95 latency",
            "Consider an HPA so load spreads instead of throttling",
        ],
        EfficiencyIssue.NO_AUTOSCALING: [
            "Tune HPA thresholds against a load test",
            "Add PodDisruptionBudget for safe scaling/upgrades",
        ],
        EfficiencyIssue.CAPACITY_RISK: [
            "Pre-scale (or schedule) capacity ahead of the campaign/peak",
            "Add HPA with a high max + headroom for the event",
        ],
    }.get(issue, ["Review the efficiency scorecard and capture action items."])


def build_optimize_graph(settings: Settings, tools: Tools, llm: LLM, logger: RunLogger,
                         memory: Memory):
    n = OptimizeNodes(settings, tools, llm, logger, memory)
    g = StateGraph(AgentState)
    g.add_node("cluster_observer", n.cluster_observer)
    g.add_node("utilization_analyzer", n.utilization_analyzer)
    g.add_node("capacity_planner", n.capacity_planner)
    g.add_node("efficiency_planner", n.efficiency_planner)
    g.add_node("recommendation_gate", n.recommendation_gate)
    g.add_node("recommendation_executor", n.recommendation_executor)
    g.add_node("validation_runner", n.validation_runner)
    g.add_node("efficiency_evaluator", n.efficiency_evaluator)
    g.add_node("efficiency_followups", n.efficiency_followups)
    g.add_node("memory_writer", n.memory_writer)

    g.add_edge(START, "cluster_observer")
    g.add_edge("cluster_observer", "utilization_analyzer")
    g.add_edge("utilization_analyzer", "capacity_planner")
    g.add_edge("capacity_planner", "efficiency_planner")
    g.add_edge("efficiency_planner", "recommendation_gate")
    g.add_edge("recommendation_gate", "recommendation_executor")
    g.add_edge("recommendation_executor", "validation_runner")
    g.add_edge("validation_runner", "efficiency_evaluator")
    g.add_edge("efficiency_evaluator", "efficiency_followups")
    g.add_edge("efficiency_followups", "memory_writer")
    g.add_edge("memory_writer", END)
    return g.compile()
