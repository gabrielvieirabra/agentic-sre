# 008 — Observability

## Problem
Autonomous runs must be inspectable and reproducible: what did the agent see, decide, do, and
prove — for debugging, evals, and postmortems.

## Goals
- Structured JSON logs with a `trace_id` per loop run.
- Before/after cluster snapshots, a full tool-call log, and a final loop report per run.
- A local run-history store for trends.

## Non-goals
- No external telemetry backends required for MVP (Prometheus/Grafana/OTel are optional).

## Requirements

### Per-run artifacts (`runs/<trace_id>/`)
```
runs/<trace_id>/
  meta.json            # goal, scenario, mode, model, start/end, terminal_state, counters
  events.jsonl         # structured log lines {ts, node, level, msg, fields}
  tools.jsonl          # audit entries from every tool call (see 005)
  snapshot_before.json # sre-lab state at start
  snapshot_after.json  # sre-lab state at end
  planned_actions.md   # all planned-action blocks
  report.md            # postmortem (also copied to reports/)
```

### Logging
- JSON structured logger; every line carries `trace_id` and current node.
- Levels: DEBUG/INFO/WARN/ERROR; `SRE_LOG_LEVEL` from env.
- `rich` renders a human-friendly live view to the terminal; files stay pure JSON.

### Run history
- SQLite (`memory/`) table `runs` indexing meta + score + terminal_state for `make report` trends.

### Optional (backlog, gated behind flags/addons)
- Prometheus + Grafana in Minikube; OpenTelemetry traces per node.

## Acceptance criteria
- Every run creates a complete `runs/<trace_id>/` directory with all listed files.
- `report.md` summarizes symptoms → diagnosis → fix → validation → terminal state.
- Logs are valid JSONL and include `trace_id` on every line.

## Risks
- Snapshot size → store summarized objects + counts, not raw manifests wholesale.
- Disk growth from many runs → `make clean` prunes; `.gitignore` excludes `runs/`.

## Open questions
- Adopt OpenTelemetry now or keep JSONL? (MVP: JSONL; OTel optional in Phase 7+.)

## Test strategy
- Run a scenario and assert all artifacts exist and parse.
- Validate JSONL schema of `events.jsonl` and `tools.jsonl`.
