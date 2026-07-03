"""Postmortem report generation (specs/008-observability.md)."""

from __future__ import annotations

from sre_agent.config import Settings
from sre_agent.observability import utc_now_iso
from sre_agent.state import AgentState


def build_report(state: AgentState, settings: Settings) -> str:
    hyp = state.hypothesis
    patch = state.proposed_patch
    base = state.baseline_validation
    val = state.validation

    lines: list[str] = []
    lines.append(f"# SRE Repair Loop — Postmortem ({state.trace_id})")
    lines.append("")
    lines.append(f"- **Generated:** {utc_now_iso()}")
    lines.append(f"- **Scenario:** {state.scenario or '(ad-hoc)'}")
    lines.append(f"- **Mode:** {state.mode}")
    lines.append(f"- **Model:** {settings.model} (fallback {settings.model_fallback})")
    ts = state.terminal_state.value if state.terminal_state else "?"
    lines.append(f"- **Terminal state:** `{ts}`")
    lines.append(f"- **Run score:** {state.eval_score}")
    lines.append(f"- **Tool calls:** {state.tool_call_count}")
    lines.append("")

    if state.alert:
        sev = state.severity.value if state.severity else "?"
        lines.append("## Alert (on-call)")
        lines.append(f"- **Severity:** `{sev}`")
        lines.append(f"- **Alert:** {state.alert.name} (signal=`{state.alert.signal}`, "
                     f"source={state.alert.source})")
        if state.alert.description:
            lines.append(f"- {state.alert.description}")
        lines.append("")

    lines.append("## Incident")
    lines.append(f"- **Category:** `{state.incident.value}`")
    if state.symptoms:
        lines.append("- **Symptoms:**")
        for s in state.symptoms:
            lines.append(f"  - {s.kind}/{s.name}: {s.detail}")
    lines.append("")

    lines.append("## Diagnosis (root cause)")
    if hyp:
        lines.append(f"- {hyp.root_cause}")
        if hyp.rationale:
            lines.append(f"- *Rationale:* {hyp.rationale}")
        lines.append(f"- *Confidence:* {hyp.confidence}")
    else:
        lines.append("- (none)")
    lines.append("")

    if state.evidence:
        lines.append("## Evidence")
        for e in state.evidence:
            lines.append(f"**{e.source}**")
            lines.append("```")
            lines.append(e.summary)
            lines.append("```")
        lines.append("")

    if state.analysis:
        an = state.analysis
        lines.append("## Efficiency scorecard")
        lines.append(f"- **App:** {an.app}  ·  **Replicas:** {an.replicas}  ·  "
                     f"**HPA:** {'yes' if an.hpa_present else 'no'}")
        lines.append(f"- **CPU:** usage {an.cpu_usage_m}m vs request {an.cpu_request_m}m "
                     f"(util {an.cpu_util_pct}%), limit {an.cpu_limit_m}m")
        lines.append(f"- **Memory:** usage {an.mem_usage_mi}Mi vs request {an.mem_request_mi}Mi "
                     f"(util {an.mem_util_pct}%)")
        lines.append(f"- **Cost units:** {an.cost_units}")
        if an.smells:
            lines.append("- **Smells:**")
            for sm in an.smells:
                lines.append(f"  - {sm}")
        if state.efficiency_issue:
            lines.append(f"- **Issue:** `{state.efficiency_issue.value}`")
        lines.append("")

    if state.load_result:
        lr = state.load_result
        lines.append("## Load test (hey)")
        lines.append(f"- rps={lr.get('rps')}  p95={lr.get('p95_ms')}ms  "
                     f"error_rate={lr.get('error_rate')}")
        lines.append("")

    if state.capacity_plan:
        cp = state.capacity_plan
        lines.append("## Capacity plan")
        lines.append(f"- Peak {cp.peak_multiplier}x → required ~{cp.required_replicas} replicas "
                     f"(current {cp.current_replicas}). {cp.note}")
        lines.append("")

    mit = state.mitigation
    rec = state.recommendation
    title = "## Recommendation" if rec else ("## Mitigation" if mit else "## Proposed fix")
    lines.append(title)
    if patch or mit or rec:
        lines.append("```")
        lines.append(state.planned_action_block)
        lines.append("```")
        if patch:
            lines.append(f"- **Patch:** `{patch.kubectl_patch}`")
        elif mit:
            lines.append(f"- **Action:** `{mit.action.value}` on "
                         f"{mit.target_kind}/{mit.target_name}")
        elif rec:
            lines.append(f"- **Action:** `{rec.action.value}` on "
                         f"{rec.target_kind}/{rec.target_name}  ·  savings: {rec.est_savings}")
    else:
        lines.append("- (none)")
    lines.append("")

    lines.append("## Actions taken")
    if state.applied_actions:
        for a in state.applied_actions:
            status = "APPLIED" if a.applied else "NOT APPLIED"
            lines.append(f"- [{status}] {a.description}")
            lines.append(f"  - cmd: `{a.command}`")
            lines.append(f"  - rollback: `{a.rollback_command}`")
    else:
        lines.append("- None (dry-run or no safe action).")
    lines.append("")

    lines.append("## Validation (before → after)")
    b_detail = base.detail if base else "n/a"
    b_healthy = base.healthy if base else "?"
    a_detail = val.detail if val else "n/a"
    a_healthy = val.healthy if val else "?"
    lines.append(f"- **Before:** {b_detail} (healthy={b_healthy})")
    lines.append(f"- **After:**  {a_detail} (healthy={a_healthy})")
    lines.append("")

    if state.recalled or state.matched_pattern:
        lines.append("## Memory")
        if state.matched_pattern:
            lines.append(f"- **Fix pattern:** {state.matched_pattern}")
        if state.recalled:
            lines.append("- **Related past incidents:**")
            for r in state.recalled:
                lines.append(f"  - {r}")
        lines.append("")

    if state.incident_timeline:
        lines.append("## Incident timeline")
        for line in state.incident_timeline:
            lines.append(f"- {line}")
        lines.append("")

    if state.followups:
        lines.append("## Follow-ups")
        for f in state.followups:
            lines.append(f"- [ ] {f}")
        lines.append("")

    lines.append("## Outcome")
    lines.append(f"- **{state.terminal_state.value if state.terminal_state else '?'}** — "
                 f"{state.escalation_reason}")
    lines.append("")
    return "\n".join(lines)
