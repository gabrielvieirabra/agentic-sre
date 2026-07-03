# Scenario: needs-hpa (efficiency / capacity)

- **Gap:** `web` has modest requests and **no HorizontalPodAutoscaler** — it cannot absorb traffic
  growth without manual scaling.
- **Detection:** no HPA present → efficiency issue `no-autoscaling`.
- **Recommendation:** **SET_HPA** — create an `autoscaling/v2` HPA (CPU target, min/max) so the
  workload scales with load.
- **Validation:** `kubectl get hpa web` exists.
- **Reset:** re-apply base + delete the HPA (`make reset` removes lab HPAs).
