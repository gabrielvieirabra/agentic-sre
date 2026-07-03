# Architecture

Narrative companion to [`specs/002-agent-architecture.md`](../specs/002-agent-architecture.md).

## Big picture
A LangGraph state machine drives a bounded SRE repair loop over the local `sre-lab` namespace.
The LLM (local Ollama) only *reasons*; all *acting* happens through contracted, audited tools
behind a safety gate. Deterministic cluster signals — not the model — decide success.

```
User goal / trigger
      │
Hardware Profiler ─ pick model tier & limits
      │
Cluster Observer ─ snapshot sre-lab
      │
Incident Classifier ─ symptoms → category
      │
Evidence Collector ─ targeted describe/logs/endpoints/metrics
      │
Hypothesis Generator (MAKER) ─ root cause + confidence
      │
Plan Generator (MAKER) ─ minimal patch + rollback + validation
      │
Safety Gate ─ mode + ns-lock + allow/deny ──► NEEDS_HUMAN (if denied)
      │
Change Executor (MAKER) ─ apply + record undo   [skipped in dry-run]
      │
Validation Runner (CHECKER) ─ re-observe + deterministic checks
      │
Evaluator / Judge ─ terminal state? ──► loop back to Evidence Collector (within guards)
      │
Memory Writer ─ persist incident + lesson
      │
Report Generator ─ postmortem + JSON run record
```

## Maker / Checker / Judge
- **Maker** proposes and applies (Hypothesis, Plan, Executor).
- **Checker** independently re-queries the cluster and runs deterministic health checks.
- **Judge** picks the terminal state from deterministic signals first; LLM-as-judge is secondary
  and weight-capped.

## State
A single typed state object flows through nodes (see spec 002). Raw kubectl output is summarized
into state; full dumps live in `runs/<trace_id>/` snapshots to keep the context small — important
on a small local model.

## Why LangGraph
Deterministic control flow (loops, conditionals, guards) with inspectable state, instead of an
open-ended agent that decides its own stopping conditions. Guards live in code, not the prompt.
