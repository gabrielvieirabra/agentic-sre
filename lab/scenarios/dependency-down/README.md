# Scenario: dependency-down (on-call)

- **App:** `depsvc` (tiny python service whose readiness depends on `MODE` from `depsvc-config`).
- **Fault:** the external dependency is "down" — `depsvc-config.mode=down`, so `/ready` returns
  503 and pods never become Ready (Service has no endpoints).
- **Alert:** `DependencyDown` (SEV2). See `lab/alerts/dependency-down.json`.
- **Mitigation (on-call):** **DEPENDENCY_FALLBACK** — patch `depsvc-config.mode=fallback` and
  `rollout restart deploy/depsvc` so pods pick up the fallback and serve again.
- **Validation:** `rollout status deploy/depsvc` + pods Ready + endpoints non-empty.
- **Reset:** re-apply `lab/manifests/depsvc` (mode=up).
