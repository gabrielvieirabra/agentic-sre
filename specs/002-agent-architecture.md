# 002 — Agent Architecture

## Problem
The agent must behave like a disciplined SRE (evidence over guesses, minimal blast radius,
validate before/after, roll back if worse), and must be orchestrated deterministically — not as
an open-ended prompt.

## Goals
- A LangGraph state machine with explicit nodes and a bounded loop-back edge.
- A **maker / checker / judge** split so fixes are validated independently of who proposed them.
- Persisted, inspectable run state for observability and evals.

## Non-goals
- No autonomous free-form shell. Every action goes through a contracted tool + safety gate.
- No multi-cluster / no non-`sre-lab` targets.

## Requirements

### Graph nodes (LangGraph `StateGraph`)
```
Trigger
  → Hardware Profiler        (annotate run with model/cluster sizing; pick model tier)
  → Cluster Observer         (snapshot sre-lab: pods, deploys, svc, events)
  → Incident Classifier      (map symptoms → incident category)
  → Evidence Collector       (targeted describe/logs/endpoints/metrics)
  → Hypothesis Generator     (MAKER: root-cause hypothesis + confidence)
  → Plan Generator           (MAKER: smallest safe patch + rollback + validation)
  → Safety Gate              (enforce mode + ns lock + allow/deny; branch to NEEDS_HUMAN)
  → Change Executor          (MAKER: apply patch, record undo)   [skipped in dry-run]
  → Validation Runner        (CHECKER: re-observe + deterministic health checks)
  → Evaluator / Checker      (JUDGE: decide terminal state or loop back)
  → Memory Writer            (persist incident, root cause, fix, lessons)
  → Report Generator         (postmortem markdown + JSON run record)
```
- **Conditional edges:** Safety Gate → `NEEDS_HUMAN` terminal if disallowed. Evaluator →
  loop back to Evidence Collector if not resolved and guards not exceeded; else terminal.
- **Roles:** Maker = Hypothesis + Plan + Executor. Checker = Validation Runner. Judge =
  Evaluator (deterministic signals first; LLM-as-judge only secondary).

### State (`src/sre_agent/state.py`)
Pydantic model / `TypedDict` reducer with at least:
`trace_id, goal, mode, scenario, cluster_snapshot, symptoms[], evidence[], hypothesis,
proposed_patch, applied_actions[], validation_results, eval_score, iteration_count,
tool_call_count, started_at, elapsed_seconds, terminal_state, escalation_reason`.

### LLM
- `src/sre_agent/llm/` wraps Ollama (`langchain-ollama`), enforces structured output via
  pydantic schemas; retries + fallback model on parse failure.

## Acceptance criteria
- The graph compiles and runs end-to-end in dry-run against a broken scenario.
- Each node reads/writes only its declared state fields (documented per node).
- Maker and checker use independent observations (checker re-queries the cluster).
- Every run terminates in exactly one named terminal state.

## Risks
- Small-model hypotheses may be wrong → mitigated by deterministic validation + loop-back + guards.
- State bloat from raw kubectl output → store summaries + pointers to snapshot files.

## Open questions
- Use LangGraph checkpointer (SQLite) for resumable runs now, or defer? (Lean: add in Phase 7.)

## Test strategy
- Unit-test each node with a fake tool layer + recorded cluster fixtures.
- Integration: full graph run per scenario asserting terminal state + health checks.
