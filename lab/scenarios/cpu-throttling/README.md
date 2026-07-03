# Scenario: cpu-throttling (SCAFFOLD — not wired for MVP)

Planned fault: CPU limit set far too low for the workload → heavy CPU throttling, slow endpoint,
high p95 latency under load.

- **Expected fix:** raise CPU requests/limits to a sane value; re-check latency under load.
- **Validation (perf):** p95 latency and error rate under `hey`/`k6` load below threshold;
  `kubectl top` shows reduced throttling.

Implement in a later chunk (see specs/004 + specs/006). This is a *performance* scenario, so its
eval uses load-test thresholds rather than a simple 200 check.
