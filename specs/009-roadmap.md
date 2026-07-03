# 009 — Roadmap & MVP Acceptance

## Problem
The build must proceed in reviewable phases with a crisp definition of "MVP done".

## Goals
- Deliver value incrementally; each phase independently verifiable.
- A clear MVP gate (12 criteria) before expanding the scenario catalog.

## Non-goals
- No premature scale-out (extra scenarios, dashboards, OTel) before the MVP loop works.

## Phases
| Phase | Deliverable | Verify |
|---|---|---|
| 1 Specs | specs 000–009 + docs + repo skeleton | 8 sections each; MVP well-defined |
| 2 Local runtime | inspect/setup scripts + `config.py` | `make inspect/setup` work; model smoke test |
| 3 Lab | `sre-lab` + 3 broken scenarios | inject reproduces symptom; reset heals |
| 4 LangGraph MVP (dry-run) | graph + tools + llm; dry-run plan | prints valid planned-action block |
| 5 Safe apply | safety gate + executor + rollback | applies in sre-lab; rolls back on regression |
| 6 Evals | runner + scoring + 3 cases + Regression Guard | `make eval` prints scores + terminal state |
| 7 Memory + reports | SQLite memory + postmortem; chaos gen; fix library | report per run; lessons stored |

**Current status:** Phases 1–3 delivered in this chunk. Phases 4–7 are the next chunks.

## MVP acceptance criteria (from product spec)
1. Runs fully locally (no cloud/paid model APIs).
2. Mac hardware profiler recommends a suitable local LLM + Minikube size.
3. Minikube lab starts successfully.
4. ≥3 broken K8s scenarios can be injected.
5. The LangGraph agent can investigate those scenarios.
6. The agent proposes a safe fix in dry-run.
7. The agent can apply a fix in apply-local-lab.
8. The checker validates the result.
9. Evals produce a score.
10. The loop stops with a named terminal state.
11. A report is generated for every run.
12. No cloud API or external model API is required.

## Creative capabilities (committed)
- **Chaos Scenario Generator** (Phase 7): parameterized controlled failures for training/evals.
- **Fix Pattern Library** (Phase 7): reusable fix templates learned from successful repairs.
- **Regression Guard** (Phase 6): re-run prior evals after each fix.
Optional backlog: incident game mode, K8s smell detector, YAML diff risk scorer, probe tuner,
resource request/limit recommender, runbook generator, production-readiness score.

## Risks
- Local model reliability could slow Phase 4 → 3B fallback + deterministic validation compensate.
- Scope creep from the large scenario catalog → catalog stays backlog until MVP passes.

## Open questions
- After MVP, prioritize more reliability scenarios or the creative features first? (Decide post-MVP.)

## Test strategy
- Each phase has its own verify column above; MVP gate = all 12 criteria pass end-to-end.
