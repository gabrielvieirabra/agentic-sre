# Scenario: bad-readiness-probe

- **Fault:** Readiness probe `httpGet.path` set to `/healthz`, which nginx serves as 404.
- **Symptom:** Pods Running but never `Ready` (0/2 Ready); Service `web` has **no endpoints**;
  HTTP requests fail.
- **Root cause:** Misconfigured readiness probe path (endpoint does not exist).
- **Expected fix (minimal):** Set the readiness probe path back to `/` (200) — or a real health
  path — via patch; do not remove the probe.
- **Deterministic validation:**
  `kubectl -n sre-lab get endpoints web` non-empty **and** in-cluster HTTP GET to `web:80` == 200.
- **Rollback:** `kubectl -n sre-lab rollout undo deploy/web` (or re-apply base).
