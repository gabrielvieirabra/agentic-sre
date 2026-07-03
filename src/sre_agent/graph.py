"""The LangGraph SRE repair loop: nodes, wiring, and the maker/checker/judge split.

See specs/002 (architecture) and specs/003 (loop engineering). Deterministic cluster
signals decide health; the local LLM proposes the diagnosis and fix. Every mutation is
gated (specs/007) and, in dry-run, is a no-op that only prints the planned-action block.
"""

from __future__ import annotations

import json
import time

from langgraph.graph import END, START, StateGraph

from sre_agent.config import Settings
from sre_agent.llm import LLM
from sre_agent.memory import Memory
from sre_agent.observability import RunLogger
from sre_agent.safety import check_gate, planned_action_block
from sre_agent.state import (
    AgentState,
    AppliedAction,
    Evidence,
    Hypothesis,
    IncidentCategory,
    ProposedPatch,
    RiskLevel,
    Symptom,
    TerminalState,
    ValidationResult,
)
from sre_agent.tools import Tools

# Known-good baseline values (mirror lab/manifests/base) used to build minimal fixes.
GOOD_IMAGE = "nginx:1.27-alpine"
GOOD_READINESS_PATH = "/"
GOOD_SELECTOR = {"app": "web"}
APP = "web"


# ---- LLM output schemas ----------------------------------------------------
from pydantic import BaseModel  # noqa: E402


class HypothesisLLM(BaseModel):
    root_cause: str
    confidence: float = 0.5
    rationale: str = ""


class PlanLLM(BaseModel):
    summary: str
    target_kind: str
    target_name: str
    kubectl_patch: str  # JSON strategic-merge patch


class Nodes:
    def __init__(self, settings: Settings, tools: Tools, llm: LLM, logger: RunLogger,
                 memory: Memory) -> None:
        self.s = settings
        self.t = tools
        self.llm = llm
        self.log = logger
        self.mem = memory
        self._t0 = time.time()  # run start, for elapsed captured inside the graph

    # ---- observation helpers ----------------------------------------
    def _snapshot(self, app: str = APP) -> dict:
        pods_res = self.t.get_json("pods")
        dep_res = self.t.get_json("deploy", app)
        svc_res = self.t.get_json("svc", app)
        ep_res = self.t.get_json("endpoints", app)

        pods = []
        for item in (pods_res.data or {}).get("items", []) if pods_res.ok else []:
            st = item.get("status", {})
            cs = (st.get("containerStatuses") or [{}])[0]
            waiting = (cs.get("state", {}) or {}).get("waiting", {}) or {}
            spec_c = (item.get("spec", {}).get("containers") or [{}])[0]
            pods.append({
                "name": item.get("metadata", {}).get("name"),
                "phase": st.get("phase"),
                "ready": bool(cs.get("ready", False)),
                "restarts": cs.get("restartCount", 0),
                "waiting_reason": waiting.get("reason"),
                "image": spec_c.get("image"),
            })

        dep = {}
        if dep_res.ok and dep_res.data:
            d = dep_res.data
            c = (d.get("spec", {}).get("template", {}).get("spec", {}).get("containers") or [{}])[0]
            dep = {
                "desired": d.get("spec", {}).get("replicas", 0),
                "ready": d.get("status", {}).get("readyReplicas", 0) or 0,
                "image": c.get("image"),
                "readiness_path": (c.get("readinessProbe", {}) or {})
                .get("httpGet", {}).get("path"),
            }

        svc = {}
        if svc_res.ok and svc_res.data:
            svc = {"selector": svc_res.data.get("spec", {}).get("selector", {})}

        ep_count = 0
        if ep_res.ok and ep_res.data:
            for subset in ep_res.data.get("subsets", []) or []:
                ep_count += len(subset.get("addresses", []) or [])

        return {"pods": pods, "deployment": dep, "service": svc, "endpoints": ep_count}

    def _validation_from(self, snap: dict) -> ValidationResult:
        dep = snap.get("deployment", {})
        desired = dep.get("desired", 0) or 0
        ready = dep.get("ready", 0) or 0
        ep = snap.get("endpoints", 0)
        bad = any(p.get("waiting_reason") for p in snap.get("pods", []))
        rollout_complete = desired > 0 and ready == desired and not bad
        healthy = rollout_complete and ep > 0
        return ValidationResult(
            healthy=healthy, rollout_complete=rollout_complete,
            ready_replicas=ready, desired_replicas=desired, endpoints=ep,
            detail=f"ready={ready}/{desired} endpoints={ep} bad_waiting={bad}",
        )

    # ---- nodes ------------------------------------------------------
    def hardware_profiler(self, state: AgentState) -> dict:
        self.log.set_node("hardware_profiler")
        self.log.info("run start", mode=self.s.mode.value, model=self.s.model,
                      fallback=self.s.model_fallback, scenario=state.scenario)
        return {}

    def cluster_observer(self, state: AgentState) -> dict:
        self.log.set_node("cluster_observer")
        snap = self._snapshot(state.target_app)
        baseline = state.baseline_validation or self._validation_from(snap)
        if state.iteration_count == 0:
            self.log.write_json("snapshot_before.json", snap)
        self.log.info("observed cluster", **{k: (len(v) if isinstance(v, list) else v)
                                             for k, v in snap.items()})
        return {"cluster_snapshot": snap, "baseline_validation": baseline}

    def incident_classifier(self, state: AgentState) -> dict:
        self.log.set_node("incident_classifier")
        snap = state.cluster_snapshot
        pods = snap.get("pods", [])
        symptoms: list[Symptom] = []
        incident = IncidentCategory.NONE

        reasons = {p.get("waiting_reason") for p in pods if p.get("waiting_reason")}
        all_ready = bool(pods) and all(p.get("ready") for p in pods)
        ep = snap.get("endpoints", 0)

        pull_errs = {"ImagePullBackOff", "ErrImagePull", "InvalidImageName"}
        if reasons & pull_errs:
            incident = IncidentCategory.IMAGE_PULL
            for p in pods:
                if p.get("waiting_reason") in pull_errs:
                    symptoms.append(Symptom(kind="pod", name=p["name"],
                                            detail=f"{p['waiting_reason']} image={p.get('image')}"))
        elif "CrashLoopBackOff" in reasons:
            incident = IncidentCategory.CRASH_LOOP
            symptoms = [Symptom(kind="pod", name=p["name"], detail="CrashLoopBackOff")
                        for p in pods if p.get("waiting_reason") == "CrashLoopBackOff"]
        elif any(p.get("phase") == "Pending" for p in pods):
            incident = IncidentCategory.PENDING_SCHEDULING
            symptoms = [Symptom(kind="pod", name=p["name"], detail="Pending (unschedulable?)")
                        for p in pods if p.get("phase") == "Pending"]
        elif ep == 0 and pods and not all_ready:
            incident = IncidentCategory.READINESS_PROBE
            symptoms = [Symptom(kind="pod", name=p["name"],
                                detail="Running but not Ready (readiness failing)")
                        for p in pods if not p.get("ready")]
            symptoms.append(Symptom(kind="service", name=APP, detail="0 endpoints"))
        elif ep == 0 and all_ready:
            incident = IncidentCategory.SERVICE_ENDPOINTS
            selector = snap.get("service", {}).get("selector")
            symptoms = [Symptom(kind="service", name=APP,
                                detail=f"0 endpoints; selector={selector}")]

        self.log.info("classified", incident=incident.value, symptoms=len(symptoms))
        return {"incident": incident, "symptoms": symptoms}

    def evidence_collector(self, state: AgentState) -> dict:
        self.log.set_node("evidence_collector")
        app = state.target_app
        ev: list[Evidence] = []
        dep = self.t.describe("deploy", app)
        if dep.ok:
            ev.append(Evidence(source=f"kubectl describe deploy/{app}",
                               summary=_tail(dep.data, 12)))
        broken = next((p for p in state.cluster_snapshot.get("pods", [])
                       if p.get("waiting_reason") or not p.get("ready")), None)
        if broken:
            desc = self.t.describe("pod", broken["name"])
            if desc.ok:
                ev.append(Evidence(source=f"kubectl describe pod/{broken['name']}",
                                   summary=_grep_events(desc.data)))
        self.log.info("collected evidence", items=len(ev))
        return {"evidence": ev}

    def hypothesis_generator(self, state: AgentState) -> dict:
        self.log.set_node("hypothesis_generator")
        # Recall past incidents of the same category to inform the diagnosis (loop memory).
        past = self.mem.recall_incidents(state.incident.value, limit=3)
        recalled = [f"{p['ts'][:19]} {p['scenario']}: {p['fix_summary']} "
                    f"-> {p['terminal_state']}" for p in past if p.get("fix_summary")]
        if recalled:
            self.log.info("recalled past incidents", count=len(recalled))
        context = {
            "incident": state.incident.value,
            "symptoms": [s.model_dump() for s in state.symptoms],
            "evidence": [e.model_dump() for e in state.evidence],
            "past_incidents": recalled,
        }
        out = self.llm.structured(
            system=("You are a senior SRE. Diagnose the single most likely root cause of a "
                    "Kubernetes issue in namespace sre-lab. Be concise and specific."),
            user="Symptoms and evidence:\n" + json.dumps(context, indent=2),
            schema=HypothesisLLM, logger=self.log,
        )
        if out is None:
            hyp = Hypothesis(root_cause=_fallback_root_cause(state.incident),
                             confidence=0.5, rationale="deterministic fallback (LLM unavailable)")
        else:
            hyp = Hypothesis(root_cause=out.root_cause, confidence=out.confidence,
                             rationale=out.rationale)
        self.log.info("hypothesis", root_cause=hyp.root_cause, confidence=hyp.confidence)
        return {"hypothesis": hyp, "recalled": recalled}

    def plan_generator(self, state: AgentState) -> dict:
        self.log.set_node("plan_generator")
        template = _template_patch(state.incident, state.cluster_snapshot)

        # Fix Pattern Library: has a patch for this incident/target worked before?
        matched = ""
        if template is not None:
            pat = self.mem.best_fix_pattern(state.incident.value, template.target_kind)
            if pat:
                matched = (f"Known fix pattern for {state.incident.value} "
                           f"({pat['successes']} prior success(es)).")
                self.log.info("matched fix pattern", **pat)

        # Ask the LLM for a structured patch; fall back to the deterministic template.
        patch = template
        if template is not None:
            context = {
                "incident": state.incident.value,
                "root_cause": state.hypothesis.root_cause if state.hypothesis else "",
                "current": state.cluster_snapshot,
                "healthy_baseline": {"image": GOOD_IMAGE, "readiness_path": GOOD_READINESS_PATH,
                                     "service_selector": GOOD_SELECTOR},
                "hint_patch": template.kubectl_patch,
            }
            out = self.llm.structured(
                system=("You are a senior SRE. Propose the SMALLEST safe fix as a kubectl "
                        "strategic-merge patch (JSON). Only touch the one broken field. "
                        "Return kubectl_patch as a compact JSON string."),
                user="Context:\n" + json.dumps(context, indent=2),
                schema=PlanLLM, logger=self.log,
            )
            if out is not None and _valid_llm_patch(out):
                patch = ProposedPatch(
                    summary=out.summary or template.summary,
                    target_kind=out.target_kind, target_name=out.target_name,
                    kubectl_patch=out.kubectl_patch,
                    rollback=template.rollback, validation=template.validation,
                    risk_level=template.risk_level,
                )
                self.log.info("plan from LLM", target=f"{patch.target_kind}/{patch.target_name}")
            else:
                self.log.info("plan from deterministic template (LLM patch unusable)")

        return {"proposed_patch": patch, "matched_pattern": matched}

    def safety_gate(self, state: AgentState) -> dict:
        self.log.set_node("safety_gate")
        patch = state.proposed_patch
        block = planned_action_block(patch, self.s) if patch else "(no fix proposed)"
        decision = check_gate(patch, self.s)
        self.log.write_text("planned_actions.md", block + "\n")
        self.log.info("safety gate", allow_apply=decision.allow_apply, reason=decision.reason)
        return {"planned_action_block": block}

    def change_executor(self, state: AgentState) -> dict:
        self.log.set_node("change_executor")
        patch = state.proposed_patch
        decision = check_gate(patch, self.s)
        if not decision.allow_apply or patch is None:
            self.log.info("no mutation (gated)", reason=decision.reason)
            return {}

        rollback_cmd = patch.rollback
        rollback_patch = ""
        # For Service selector changes, capture the current selector for a precise rollback.
        if patch.target_kind == "Service":
            cur = state.cluster_snapshot.get("service", {}).get("selector", {})
            rollback_patch = json.dumps({"spec": {"selector": cur}})
            rollback_cmd = (f"kubectl -n {self.s.namespace} patch svc {patch.target_name} "
                            f"--type strategic -p '{rollback_patch}'")

        res = self.t.patch(patch.target_kind, patch.target_name, patch.kubectl_patch,
                           rollback=rollback_cmd)
        action = AppliedAction(
            description=patch.summary,
            command=f"kubectl -n {self.s.namespace} patch {patch.target_kind} "
                    f"{patch.target_name} -p '{patch.kubectl_patch}'",
            rollback_command=rollback_cmd, applied=res.ok,
            target_kind=patch.target_kind, target_name=patch.target_name,
            rollback_patch=rollback_patch,
        )
        if res.ok:
            self.log.info("applied fix", target=f"{patch.target_kind}/{patch.target_name}")
            if patch.target_kind == "Deployment":
                self.t.rollout_status(f"deploy/{patch.target_name}", timeout_s=90)
        else:
            self.log.error("apply failed", error=res.error)
        return {"applied_actions": [action]}

    def validation_runner(self, state: AgentState) -> dict:
        self.log.set_node("validation_runner")
        applied = any(a.applied for a in state.applied_actions)
        # Independent re-observation (checker). If a fix was applied, poll until the
        # cluster converges (e.g. the endpoints controller repopulates after a Service
        # selector change) instead of judging on a single racy read.
        app = state.target_app
        snap = self._snapshot(app)
        val = self._validation_from(snap)
        if applied:
            for _ in range(10):
                if val.healthy:
                    break
                time.sleep(2)
                snap = self._snapshot(app)
                val = self._validation_from(snap)
        self.log.info("validation", healthy=val.healthy, detail=val.detail)
        return {"cluster_snapshot": snap, "validation": val}

    def evaluator(self, state: AgentState) -> dict:
        self.log.set_node("evaluator")
        applied = any(a.applied for a in state.applied_actions)
        val = state.validation or state.baseline_validation
        terminal: TerminalState
        reason = ""

        if state.incident in (IncidentCategory.NONE,) and not state.symptoms:
            terminal = TerminalState.NO_ACTION_NEEDED
            reason = "cluster healthy; nothing to do"
        elif applied and val and val.healthy:
            terminal = TerminalState.FIXED
            reason = "fix applied and validated healthy"
        elif applied and (not val or not val.healthy):
            # fix did not restore health -> roll back to avoid leaving it worse
            self._rollback(state)
            terminal = TerminalState.ROLLED_BACK
            reason = "applied fix did not validate healthy; rolled back"
        elif not applied and state.proposed_patch is not None:
            terminal = TerminalState.NEEDS_HUMAN
            reason = ("dry-run: fix proposed but not applied (safety gate blocks mutation "
                      "outside apply-local-lab)")
        else:
            terminal = TerminalState.FAILED_SAFELY
            reason = "no safe fix could be proposed"

        score = _score(state, terminal)
        self.log.info("evaluated", terminal=terminal.value, reason=reason, score=score)
        return {"terminal_state": terminal, "escalation_reason": reason, "eval_score": score,
                "tool_call_count": self.t.calls,
                "elapsed_seconds": round(time.time() - self._t0, 2)}

    def _rollback(self, state: AgentState) -> None:
        for a in state.applied_actions:
            if not a.applied:
                continue
            self.log.warn("rolling back", target=f"{a.target_kind}/{a.target_name}")
            # rollback is itself a mutation; use structured, bounded operations only.
            if a.target_kind == "Deployment":
                self.t.rollout_undo(f"deploy/{a.target_name}")
                self.t.rollout_status(f"deploy/{a.target_name}", timeout_s=60)
            elif a.target_kind == "Service" and a.rollback_patch:
                self.t.patch("Service", a.target_name, a.rollback_patch,
                             rollback="(rollback of a rollback is a no-op)")

    def memory_writer(self, state: AgentState) -> dict:
        self.log.set_node("memory_writer")
        lesson = {
            "scenario": state.scenario, "incident": state.incident.value,
            "root_cause": state.hypothesis.root_cause if state.hypothesis else None,
            "terminal_state": state.terminal_state.value if state.terminal_state else None,
            "fix": state.proposed_patch.summary if state.proposed_patch else None,
        }
        self.log.write_json("lesson.json", lesson)
        # Persist to the local SQLite memory (incidents + Fix Pattern Library).
        try:
            self.mem.record_run(state)
        except Exception as e:  # noqa: BLE001 - memory must never break the loop
            self.log.warn("memory write failed", error=str(e))
        self.log.info("memory written", **{k: v for k, v in lesson.items() if v})
        return {}


# ---- helpers ---------------------------------------------------------------
def _tail(text: str, n: int) -> str:
    lines = (text or "").strip().splitlines()
    return "\n".join(lines[-n:])


def _grep_events(describe_text: str) -> str:
    lines = (describe_text or "").splitlines()
    keep = [ln for ln in lines if any(k in ln for k in
            ("Events:", "Warning", "Failed", "Back-off", "Error", "Pulling", "pull"))]
    return "\n".join(keep[-12:]) or _tail(describe_text, 8)


def _fallback_root_cause(incident: IncidentCategory) -> str:
    return {
        IncidentCategory.IMAGE_PULL: "Deployment references an image tag that cannot be pulled.",
        IncidentCategory.READINESS_PROBE: "Readiness probe is failing, so pods never become Ready "
                                          "and the Service has no endpoints.",
        IncidentCategory.SERVICE_ENDPOINTS: "Service selector does not match pod labels, so the "
                                            "Service has no endpoints.",
        IncidentCategory.CRASH_LOOP: "Container is crash-looping.",
        IncidentCategory.PENDING_SCHEDULING: "Pod cannot be scheduled.",
    }.get(incident, "Unknown root cause.")


def _template_patch(incident: IncidentCategory, snap: dict) -> ProposedPatch | None:
    if incident is IncidentCategory.IMAGE_PULL:
        patch = {"spec": {"template": {"spec": {"containers": [
            {"name": APP, "image": GOOD_IMAGE}]}}}}
        return ProposedPatch(
            summary=f"Set Deployment {APP} image to a valid tag ({GOOD_IMAGE}).",
            target_kind="Deployment", target_name=APP, kubectl_patch=json.dumps(patch),
            rollback=f"kubectl -n sre-lab rollout undo deploy/{APP}",
            validation=f"kubectl -n sre-lab rollout status deploy/{APP} --timeout=90s",
            risk_level=RiskLevel.LOW)
    if incident is IncidentCategory.READINESS_PROBE:
        probe = {"httpGet": {"path": GOOD_READINESS_PATH, "port": 80}}
        patch = {"spec": {"template": {"spec": {"containers": [
            {"name": APP, "readinessProbe": probe}]}}}}
        return ProposedPatch(
            summary=f"Fix readiness probe path to '{GOOD_READINESS_PATH}' on Deployment {APP}.",
            target_kind="Deployment", target_name=APP, kubectl_patch=json.dumps(patch),
            rollback=f"kubectl -n sre-lab rollout undo deploy/{APP}",
            validation=f"kubectl -n sre-lab get endpoints {APP} (non-empty) + rollout status",
            risk_level=RiskLevel.LOW)
    if incident is IncidentCategory.SERVICE_ENDPOINTS:
        patch = {"spec": {"selector": GOOD_SELECTOR}}
        return ProposedPatch(
            summary=f"Align Service {APP} selector to {GOOD_SELECTOR}.",
            target_kind="Service", target_name=APP, kubectl_patch=json.dumps(patch),
            rollback="restore previous Service selector (captured at apply time)",
            validation=f"kubectl -n sre-lab get endpoints {APP} (non-empty)",
            risk_level=RiskLevel.LOW)
    return None


def _valid_llm_patch(out: PlanLLM) -> bool:
    if out.target_kind not in {"Deployment", "Service"} or out.target_name != APP:
        return False
    try:
        obj = json.loads(out.kubectl_patch)
    except (json.JSONDecodeError, TypeError):
        return False
    return isinstance(obj, dict) and set(obj.keys()) <= {"spec", "metadata"}


def _score(state: AgentState, terminal: TerminalState) -> float:
    # Lightweight run score; the eval suite (Phase 6) does rigorous rubric scoring.
    if terminal is TerminalState.FIXED:
        return 1.0
    if terminal is TerminalState.NO_ACTION_NEEDED:
        return 1.0
    if terminal is TerminalState.NEEDS_HUMAN and state.proposed_patch is not None:
        return 0.6  # correct diagnosis + proposed fix, not applied (dry-run)
    if terminal is TerminalState.ROLLED_BACK:
        return 0.3
    return 0.0


# ---- routing + assembly ----------------------------------------------------
def _route_after_classify(state: AgentState) -> str:
    return "evaluator" if state.incident is IncidentCategory.NONE else "evidence_collector"


def build_graph(settings: Settings, tools: Tools, llm: LLM, logger: RunLogger, memory: Memory):
    n = Nodes(settings, tools, llm, logger, memory)
    g = StateGraph(AgentState)

    g.add_node("hardware_profiler", n.hardware_profiler)
    g.add_node("cluster_observer", n.cluster_observer)
    g.add_node("incident_classifier", n.incident_classifier)
    g.add_node("evidence_collector", n.evidence_collector)
    g.add_node("hypothesis_generator", n.hypothesis_generator)
    g.add_node("plan_generator", n.plan_generator)
    g.add_node("safety_gate", n.safety_gate)
    g.add_node("change_executor", n.change_executor)
    g.add_node("validation_runner", n.validation_runner)
    g.add_node("evaluator", n.evaluator)
    g.add_node("memory_writer", n.memory_writer)

    g.add_edge(START, "hardware_profiler")
    g.add_edge("hardware_profiler", "cluster_observer")
    g.add_edge("cluster_observer", "incident_classifier")
    g.add_conditional_edges("incident_classifier", _route_after_classify,
                            {"evaluator": "evaluator", "evidence_collector": "evidence_collector"})
    g.add_edge("evidence_collector", "hypothesis_generator")
    g.add_edge("hypothesis_generator", "plan_generator")
    g.add_edge("plan_generator", "safety_gate")
    g.add_edge("safety_gate", "change_executor")
    g.add_edge("change_executor", "validation_runner")
    g.add_edge("validation_runner", "evaluator")
    g.add_edge("evaluator", "memory_writer")
    g.add_edge("memory_writer", END)
    return g.compile()
