# 011 — Efficiency, Capacity Planning & Cost

## Problem
Systems fail from *growth, waste, and bottlenecks*, not only from breakage. The SRE prevents that:
right-size resources, tune autoscaling, plan for peaks, and cut waste — before an outage or a
surprise bill. This is a distinct, **analysis-first, low-blast-radius** loop.

## Goals
- A third LangGraph (`sre-agent optimize`) alongside repair/on-call (zero regression), reusing the
  observe/validation/memory nodes.
- Deterministic **utilization analysis** (kubectl top + request math) → an **efficiency scorecard**.
- Bounded, gated actions: **right-size (down/up), set/tune HPA, adjust replicas** for capacity.
- A local **cost model** (cost-units, optionally dollarized) with before/after savings.
- **Capacity planning** for a target peak multiplier; best-effort **load test** (`hey`) as evidence.

## Non-goals / out of local scope (documented backlog)
Karpenter/node autoscaling, NAT/log/storage/egress cost, slow-query review, cache tuning, queue
consumer scaling, timeouts/retries/circuit breakers. These are cloud- or app-level and would be
**simulated later** (e.g. via `depsvc`); the local loop covers the K8s-observable compute core.

## Requirements

### Cost model (`cost.py`)
`cost_units = replicas × (cpu_request_m + mem_request_Mi × weight)` — a deterministic proxy for
reserved compute. Optional `dollars_per_month` from a configurable price table
(`SRE_PRICE_VCPU_HOUR`, `SRE_PRICE_GIB_HOUR`; 0 → units only). Config also: `SRE_MEM_COST_WEIGHT`,
`SRE_CPU_TARGET_UTIL`, `SRE_PEAK_MULTIPLIER`.

### Analysis + scorecard (deterministic)
From `kubectl top` (usage) vs requests/limits + replicas + HPA presence: cpu/mem utilization %,
cost-units, and **smells** (no requests set, over-provisioned, throttle-risk limit, no HPA, single
replica). Classify `EfficiencyIssue`: over-provisioned / under-provisioned / no-autoscaling /
capacity-risk / efficient (priority: under > over > no-autoscaling; capacity-risk overrides
efficient/no-autoscaling when a peak needs more replicas).

### Catalog (issue → action)
| Issue | Action | Change |
|---|---|---|
| over-provisioned | RIGHT_SIZE_DOWN | requests ≈ usage×2 (floors 10m/16Mi), limits ×2 → cost drop |
| under-provisioned / throttling | RIGHT_SIZE_UP | raise CPU limit |
| no-autoscaling | SET_HPA | autoscaling/v2 HPA (CPU target, min/max) |
| capacity-risk | ADJUST_REPLICAS | scale to required_replicas for the peak |

Action is **deterministic** (high-stakes); the LLM only refines the one-line summary.

### Capacity math
`required = min(MAX, max(current, ceil(current × peak × max(util,0.25) / target_util)))`.
The 0.25 utilization floor keeps idle workloads from trivializing peak planning.

### Safety (extends 007)
`check_recommendation_gate`: apply mode + action allowed + target a lab Deployment (`web`/`depsvc`)
or its HPA + replicas 1..5 + HPA bounds sane + valid JSON resources patch + rollback + validation.

## Acceptance criteria
- `optimize --scenario over-provisioned --mode apply-local-lab` → RIGHT_SIZE_DOWN → pods Ready,
  requests reduced, **cost-units drop** → IMPROVED.
- `needs-hpa` → SET_HPA → `hpa/web` exists (autoscaling/v2) → IMPROVED.
- `cpu-throttling` → RIGHT_SIZE_UP under load → IMPROVED. `--peak N` → ADJUST_REPLICAS → IMPROVED.
- dry-run prints the scorecard + planned change + savings and does not mutate; already-efficient → NO_ACTION_NEEDED.

## Risks
- Local memory pressure (16 GB, Docker VM ~7.75 GiB) → apiserver/metrics can be slow/flaky; the loop
  degrades gracefully and recommendations are re-runnable.
- `kubectl top` needs metrics-server warm; load test is best-effort and skips on failure.

## Open questions
- Add VPA-style request recommendations from historical p95? (backlog)
- Wire node-level cost / bin-packing analysis? (needs multi-node scheduling; backlog)

## Test strategy
- Unit (no cluster/LLM): cost parsing + units + dollarization, capacity math, analyzer
  classification + smells, catalog issue→action, recommendation gate, report rendering.
- Live: the scenarios above in apply mode; dry-run no-mutation; forced-unhealthy → ROLLED_BACK.
