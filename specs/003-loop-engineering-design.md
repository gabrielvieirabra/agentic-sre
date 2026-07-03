# 003 — Loop Engineering Design

## Problem
One-off prompts don't compose or stay safe. We need reusable, bounded loops with explicit
contracts, stopping rules, rollback, and named terminal states.

## Goals
- A declarative loop contract reused across scenarios.
- Hard guards so the loop **never runs forever**.
- Clear maker/checker/judge division and escalation.

## Non-goals
- No open-ended agent that decides its own stopping conditions at runtime.

## The loop contract

```yaml
loop_name: k8s_sre_repair_loop
trigger: >
  Manual (make run-agent SCENARIO=...) or a detected abnormal state in sre-lab.
goal: >
  Restore the affected sre-lab workload to a healthy, validated state with the
  smallest safe change; otherwise escalate or fail safely.
context_inputs:
  - cluster_snapshot (pods/deploys/svc/events in sre-lab)
  - scenario metadata (expected symptoms, validation command)
  - memory (past incidents / fix patterns)
  - hardware profile (model tier, limits)
tools_allowed:            # see 005-tool-contracts.md
  - kubectl_get, kubectl_describe, kubectl_logs, kubectl_rollout_status
  - kubectl_apply, kubectl_patch, kubectl_rollout_undo   # mutating: gated
  - run_http_check, collect_metrics, minikube_status
  - read_file, write_file, run_shell_command (sandboxed allowlist)
actions_allowed:
  - Observe / describe / read logs / metrics (always)
  - Apply/patch/rollout-undo ONLY in sre-lab, only in apply-local-lab mode, only when
    rollback + validation are defined
verification:
  - Deterministic: rollout status Complete; pods Ready == desired; endpoints non-empty;
    HTTP 200 on the app; error/latency thresholds under load (perf scenarios)
  - Secondary: LLM-as-judge on explanation quality (never sole source of truth)
stopping_rules:
  - max_iterations: 6
  - max_tool_calls: 40
  - max_elapsed_seconds: 600
  - stop immediately on terminal state
rollback_strategy:
  - Prefer kubectl rollout undo for Deployments; else re-apply the recorded prior manifest.
  - If validation is worse than the pre-fix baseline, roll back and mark ROLLED_BACK.
memory_written:
  - symptoms, classified incident, hypothesis, applied fix, validation result,
    success/failure, reusable lesson / fix pattern
human_escalation_conditions:
  - Safety gate denies the only viable action
  - Confidence below threshold after guards exhausted
  - Repeated failed fixes (no progress across iterations)
  - Action would touch a non-sre-lab resource or unknown resource
terminal_states:
  - FIXED               # issue resolved and validated
  - IMPROVED            # measurable improvement, not fully resolved
  - MITIGATED           # on-call: bleeding stopped, root cause -> follow-up (see 010)
  - NO_ACTION_NEEDED    # already healthy / false alarm
  - NEEDS_HUMAN         # escalated
  - FAILED_SAFELY       # could not fix, no harm done
  - ROLLED_BACK         # fix made it worse, reverted
```

## Requirements
- Guards enforced in code (not just prompt): iteration/tool/time counters in state.
- Every mutating action records an undo before applying.
- Judge chooses terminal state from deterministic signals first.

## Acceptance criteria
- A scenario that cannot be fixed still terminates (FAILED_SAFELY / NEEDS_HUMAN) within guards.
- A fix that regresses the baseline results in ROLLED_BACK.
- No run exceeds max_iterations / max_tool_calls / max_elapsed_seconds.

## Risks
- Guard values too tight → premature give-up; too loose → wasted local compute. Tunable via env.

## Open questions
- Should IMPROVED require a quantified delta threshold per scenario? (Yes for perf scenarios.)

## Test strategy
- Force each terminal state with a crafted scenario/fixture and assert it is reached.
- Assert counters stop the loop when limits are hit.
