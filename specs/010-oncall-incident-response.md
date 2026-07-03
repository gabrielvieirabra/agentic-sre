# 010 — On-Call / Incident Response & Mitigation

## Problem
The repair loop (002/003) fixes root-cause misconfigurations. But the SRE on call engages when
something *breaks or degrades* and must **stop the bleeding fast** — triage an alert, mitigate,
communicate, and open follow-ups — deferring the root-cause fix. That is a different loop.

## Goals
- An alert-driven **incident-response loop** distinct from the repair loop (zero regression to it).
- Mitigate via a bounded catalog: **rollback / canary-abort, emergency scale-out, rollout restart,
  config-adjust / dependency-fallback**.
- Triage by **severity (SEV1–4)**; **communicate** in an incident channel; open **follow-ups**.
- Reuse observation/evidence/diagnosis/memory nodes; stay local + `sre-lab`-scoped + gated.

## Non-goals
- No real paging/Slack/PagerDuty/cloud (a local transcript + off-by-default hooks only).
- Not a root-cause fixer (that is the repair loop). Not multi-service orchestration.

## Requirements

### Loop contract
```yaml
loop_name: k8s_oncall_incident_loop
trigger: an alert (JSON file, Alertmanager-ish) OR auto-detected from cluster golden signals
goal: mitigate the incident in sre-lab (stop the bleeding), communicate, and open follow-ups
context_inputs: [alert, cluster snapshot, evidence (incl. kubectl top), memory (past incidents)]
tools_allowed: [get/describe/logs/top/rollout_status,  rollout_undo/scale/rollout_restart/patch (gated)]
actions_allowed: mitigations in sre-lab only, apply-local-lab only, rollback+validation required
verification: deterministic — rollout complete, ready replicas >= target, endpoints, pods Ready
stopping_rules: single pass (mitigation is decisive); guards inherited (tool/time budgets)
rollback_strategy: scale->prior replicas; config->restore ConfigMap; restart->n/a; rollback->escalate
memory_written: incident + mitigation action + outcome (Fix Pattern Library) + follow-ups
human_escalation_conditions: gate denies; rollback did not recover; no safe mitigation
terminal_states: [MITIGATED, ROLLED_BACK, NEEDS_HUMAN, FAILED_SAFELY, NO_ACTION_NEEDED]
```

### Severity (deterministic)
From the alert file if given, else mapped by signal: DeployFailed/DependencyDown → SEV2,
HighLatency/PodsDegraded → SEV3. (SEV1 reserved for full outage; SEV4 for low-impact.)

### Mitigation catalog (signal → action)
| Signal | Incident | Action | Target |
|---|---|---|---|
| DeployFailed / BadDeploy | bad-deploy | ROLLBACK (`rollout undo`) | Deployment |
| HighLatency / Overload | overload | SCALE_OUT (`scale --replicas`) | Deployment |
| PodsDegraded / ConfigDrift | config-drift | RESTART (`rollout restart`) | Deployment |
| DependencyDown / Upstream5xx | dependency-down | DEPENDENCY_FALLBACK (patch ConfigMap + restart) | ConfigMap |

The **action is deterministic** (high-stakes); the LLM only refines the human-readable summary.

### Safety (extends 007)
`check_mitigation_gate`: apply mode + action in the allowed set + target a known lab resource
(`web`/`depsvc` Deployment, `depsvc-config` ConfigMap) + rollback + validation defined + scale
replicas in 1..5 + valid JSON config patch. Standard planned-action block printed before mutating.

### Comms & follow-ups (local by default)
`runs/<id>/incident_channel.md` — timestamped timeline (🚨 DECLARED → 🔎 INVESTIGATING →
🛠️ MITIGATING → ✅ RESOLVED / ⚠️ ESCALATED). `runs/<id>/followups.md` + SQLite `followups` table.
A `notify()` seam logs locally; Slack/webhook and GitHub issues are off-by-default extension points.

## Acceptance criteria
- `sre-agent oncall --scenario <s> [--alert f.json] --mode <m>` runs end to end and terminates in a
  named terminal state; dry-run prints the planned mitigation and does not mutate.
- bad-deploy → ROLLBACK → MITIGATED; overloaded → SCALE_OUT (ready replicas = 3) → MITIGATED;
  config-drift → RESTART → MITIGATED; dependency-down → DEPENDENCY_FALLBACK → MITIGATED.
- Every run writes an incident channel transcript + follow-ups; memory records the incident.

## Risks
- Small-model summary drift → action is deterministic; summary is cosmetic.
- Mitigation that doesn't recover → auto-rollback (scale/config) or escalate (rollback) — never left worse.
- Auto-detect can misread → prefer the alert file for exact scenarios.

## Open questions
- Add SEV1 full-outage handling with faster/looser guards? (Deferred.)
- Wire the real Slack/GitHub hooks behind flags? (Deferred; kept as seams to preserve no-cloud.)

## Test strategy
- Unit (no cluster/LLM): gate allow/deny, catalog signal→action, severity map, report rendering.
- Live: the four scenarios above in apply mode; dry-run no-mutation.
- Escalation demo (`bad-deploy-unrecoverable`): two consecutive bad deploys → ROLLBACK reverts to a
  still-broken revision → `NEEDS_HUMAN` (the loop never auto-undoes a rollback; it escalates and
  leaves the service no worse). Non-rollback mitigations that fail instead auto-revert → `ROLLED_BACK`.
