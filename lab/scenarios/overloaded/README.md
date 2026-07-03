# Scenario: overloaded (on-call)

- **Fault:** `web` scaled down to **1 replica** → a single pod carries all traffic (high latency,
  no redundancy).
- **Alert:** `HighLatency` (SEV3). See `lab/alerts/high-latency.json`.
- **Mitigation (on-call):** **SCALE_OUT** — `kubectl scale deploy/web --replicas=3` to shed load.
  (Adding an HPA is a follow-up.)
- **Validation:** ready replicas ≥ 3 for `deploy/web` + endpoints non-empty.
- **Reset:** re-apply base (replicas=2).
