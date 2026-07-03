# Scenario: wrong-image-tag

- **Fault:** Deployment `web` references `nginx:9.9.9-doesnotexist`.
- **Symptom:** Pods stuck in `ImagePullBackOff` / `ErrImagePull`; rollout never completes.
- **Root cause:** Non-existent image tag.
- **Expected fix (minimal):** Set the image tag back to a valid one (e.g. `nginx:1.27-alpine`)
  via `kubectl set image` / patch, or re-apply the base.
- **Deterministic validation:**
  `kubectl -n sre-lab rollout status deploy/web --timeout=90s` → Complete, pods Ready == 2.
- **Rollback:** `kubectl -n sre-lab rollout undo deploy/web` (or re-apply base).
