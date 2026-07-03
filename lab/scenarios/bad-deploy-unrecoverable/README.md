# Scenario: bad-deploy-unrecoverable (on-call — escalation demo)

- **Fault:** **two** consecutive bad deploys of `web` (`nginx:1.0-bad-a` then
  `nginx:2.0-bad-b`, both unpullable). Injected via `kubectl set image` twice so the rollout
  history has two broken revisions on top of the healthy baseline.
- **Alert:** `DeployFailed` (SEV2) — reuse `lab/alerts/bad-deploy.json`.
- **Mitigation attempted:** **ROLLBACK** (`rollout undo`) — but it reverts to the *previous*
  revision, which is **also broken** (`bad-a`), so the service does not recover.
- **Expected outcome:** **`NEEDS_HUMAN`** — the loop will not auto-undo a rollback (that could
  make things worse), so it escalates. The incident channel shows `⚠️ ESCALATED / NOT RESOLVED`.
- **Why:** demonstrates safe escalation when the first-line mitigation is insufficient.
- **Reset:** re-apply base (healthy v1). Note: injected via `set image`, so `make reset` heals it.

Run:
```
make inject SCENARIO=bad-deploy-unrecoverable
make oncall SCENARIO=bad-deploy-unrecoverable ALERT=lab/alerts/bad-deploy.json MODE=apply-local-lab
```
