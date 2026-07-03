# Scenario: bad-deploy (on-call)

- **Fault:** a new rollout ("v2") sets image `nginx:2.0-broken-deploy` (unpullable) on top of the
  healthy baseline → the rollout fails (ImagePullBackOff), app degraded.
- **Alert:** `DeployFailed` (SEV2). See `lab/alerts/bad-deploy.json`.
- **Mitigation (on-call):** **ROLLBACK** — `kubectl rollout undo deploy/web` to the previous good
  revision. (Root-cause fix of v2 is a follow-up, not the on-call job.)
- **Validation:** `rollout status deploy/web` Complete + pods Ready + endpoints non-empty.
- **Reset:** re-apply base (healthy v1).
