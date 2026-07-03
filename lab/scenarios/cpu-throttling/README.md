# Scenario: cpu-throttling (efficiency / performance)

- **Bottleneck:** `web` CPU limit set to `20m` — far too low; under load the container is CPU
  throttled (high latency).
- **Detection:** tiny CPU limit (≤ 30m) or usage near limit → efficiency issue `under-provisioned`.
- **Recommendation:** **RIGHT_SIZE_UP** — raise the CPU limit (≈ max(100m, usage×3)).
- **Validation:** pods Ready; under a `--load` test, p95 latency improves and throttling drops.
- **Reset:** re-apply base (limit 100m).
