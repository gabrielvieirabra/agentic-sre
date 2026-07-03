# 006 — Evaluation Strategy

## Problem
We need objective, repeatable evidence that the agent actually fixes issues — not a vibe check.
LLM-as-judge alone is untrustworthy as ground truth.

## Goals
- Deterministic checks are the source of truth; LLM-as-judge is a secondary signal only.
- One eval case per scenario with an explicit rubric and expected terminal state.
- A **Regression Guard**: re-run prior eval cases after each new fix/change.

## Non-goals
- No reliance on model self-report for pass/fail.
- No benchmarking against cloud services.

## Requirements

### Eval case shape (`evals/cases/eval_00N_*.yaml`)
```yaml
name: eval_001_wrong_image_tag
scenario: wrong-image-tag
expected_diagnosis: "Deployment references a non-existent image tag -> ImagePullBackOff"
acceptable_fixes:
  - "set image tag to a valid one"
  - "kubectl set image / patch to valid tag"
validation_command: "kubectl -n sre-lab rollout status deploy/echo --timeout=90s"
expected_terminal_state: FIXED
rubric:                       # weights sum to 1.0
  correct_diagnosis: 0.25
  minimal_safe_fix: 0.20
  successful_validation: 0.25
  no_unrelated_changes: 0.10
  rollback_available: 0.05
  explanation_quality: 0.10
  time_tool_efficiency: 0.05
```

### Deterministic checks (scoring inputs)
- `kubectl rollout status` Complete
- pods Ready == desired replicas
- Service endpoints non-empty
- HTTP 200 on the app endpoint
- error rate / p95 latency under load below threshold (perf scenarios)
- manifest diff limited to the expected resource/field (no unrelated changes)
- resource usage before/after (perf)

### MVP eval cases
`eval_001_wrong_image_tag`, `eval_002_bad_readiness_probe`, `eval_003_service_selector_mismatch`.
Scaffolded: `eval_004_cpu_throttling`, `eval_005_pending_pod_node_selector`,
`eval_006_missing_configmap`, `eval_007_networkpolicy_blocks_app`, `eval_008_hpa_bad_threshold`.

### Scoring (`evals/scoring.py`)
- Each rubric dimension → 0..1, mostly from deterministic checks; `explanation_quality` may use
  LLM-as-judge (secondary) but is capped in weight.
- Overall = weighted sum. Pass threshold configurable (default ≥ 0.8 **and** terminal state matches).

### Runner (`evals/runner.py`)
- For each case: reset lab → inject scenario → run agent (apply-local-lab) → run deterministic
  checks → score → record. Then **Regression Guard**: re-run previously-passing cases.
- Emits a JSON history + a rich table summary.

## Acceptance criteria
- `make eval` runs all MVP cases and prints per-dimension + overall scores and terminal state.
- A deliberately wrong fix scores low on `successful_validation` and does not pass.
- Regression Guard flags if a change breaks a previously-passing case.

## Risks
- Cluster flakiness → checks poll with timeouts; cases reset the lab first.
- Overfitting the agent to exact fix strings → `acceptable_fixes` is a set; validation is behavioral.

## Open questions
- Store eval history in SQLite or JSONL? (MVP: JSONL under `runs/`, index in SQLite in Phase 7.)

## Test strategy
- Self-test scoring with synthetic inputs (known-good and known-bad) → expected scores.
