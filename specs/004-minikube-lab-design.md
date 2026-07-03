# 004 — Minikube Lab Design

## Problem
We need a small, reproducible set of intentionally broken Kubernetes workloads to exercise and
evaluate the repair loop — safe, local, and fast to reset.

## Goals
- A single `sre-lab` namespace with tiny workloads (nginx / small python http).
- Each scenario expressed as **healthy `base/` + broken overlay** (Kustomize) so injection and
  reset are deterministic and diffable.
- Trivial inject / reset / redeploy via scripts + `make`.

## Non-goals
- No heavy apps, no databases, no multi-node clusters, no persistent volumes for MVP.
- No scenario that could affect anything outside `sre-lab`.

## Requirements

### Namespace & baseline
- Namespace: `sre-lab` (created by `setup_minikube.sh`).
- Baseline healthy app: a small nginx Deployment + Service (+ optional Ingress).
- Images: pinned public images already small (e.g. `nginx:1.27-alpine`).
- **Deployment strategy: `Recreate`** (not RollingUpdate). Verified live: with RollingUpdate a
  fault injected over a healthy deployment leaves the old pods serving, so `endpoints` stays
  non-empty and can't distinguish broken from fixed. `Recreate` makes each fault a decisive
  outage (0 endpoints), so the deterministic validations below are actually discriminating.

### Scenario model
```
lab/
  manifests/base/                 # healthy app (Deployment, Service)
  scenarios/<name>/
    kustomization.yaml            # patches base to introduce ONE fault
    README.md                     # symptom, root cause, expected fix, validation
```

### MVP scenarios (full end-to-end)
| Scenario | Fault | Symptom | Expected fix | Deterministic validation |
|---|---|---|---|---|
| `wrong-image-tag` | image tag that doesn't exist | ImagePullBackOff / ErrImagePull | set correct tag | rollout status Complete + pods Ready |
| `bad-readiness-probe` | probe wrong path/port | pods never Ready, 0 endpoints | correct probe path/port | endpoints non-empty + HTTP 200 |
| `service-selector-mismatch` | Service selector ≠ pod labels | Service has no endpoints | align selector | `kubectl get endpoints` non-empty + HTTP 200 |

### Scaffolded (spec/stub only for MVP)
`missing-configmap`, `cpu-throttling`, and the broader catalog below.

### Catalog (backlog, from product spec)
Reliability: crashloop, liveness-too-aggressive, missing-secret, ingress mismatch, low limits,
HPA misconfig, impossible nodeSelector (pending), DNS failure, NetworkPolicy block.
Performance: CPU throttling, memory pressure, slow endpoint, bad requests/limits, too many
replicas, inefficient startup probe, overloaded single pod, artificial latency endpoint.

### Scripts
- `run_lab.sh` — apply healthy baseline to `sre-lab`.
- `inject_bug.sh <scenario>` — `kubectl apply -k lab/scenarios/<scenario>` (ns-locked).
- `reset_lab.sh` — delete lab resources / re-apply baseline to healthy.
All scripts refuse to run against any context ≠ `minikube` or any ns ≠ `sre-lab`.

## Acceptance criteria
- `make lab-up` yields a healthy app (pods Ready, HTTP 200).
- `make inject SCENARIO=<name>` reliably reproduces the documented symptom for all 3 MVP scenarios.
- `make reset` restores health.

## Risks
- Image pull time on first run → pre-pull in setup; keep images tiny.
- Flaky readiness timing → validation polls with a timeout, not a single check.

## Open questions
- Use Ingress (needs addon) or stick to Service + port-forward for HTTP checks? (MVP: Service +
  in-cluster/port-forward check to avoid Ingress addon dependency.)

## Test strategy
- For each MVP scenario: inject → assert broken state signature → reset → assert healthy.
