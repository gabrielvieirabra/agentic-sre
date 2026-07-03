# Scenario: service-selector-mismatch

- **Fault:** Service `web` selector is `app: web-broken`, but pods are labeled `app: web`.
- **Symptom:** Pods are healthy and Ready, but Service `web` has **no endpoints**; traffic fails.
- **Root cause:** Service selector does not match any pod labels.
- **Expected fix (minimal):** Align the Service selector back to `app: web` via patch.
- **Deterministic validation:**
  `kubectl -n sre-lab get endpoints web` non-empty **and** in-cluster HTTP GET to `web:80` == 200.
- **Rollback:** re-apply base Service (or patch selector back). Note: Services have no rollout;
  rollback = re-apply the known-good selector.
