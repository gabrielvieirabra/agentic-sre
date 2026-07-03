# sre-agent — Local-First SRE Agentic Platform

An autonomous SRE loop that **detects → investigates → fixes → validates → documents** issues
inside a local **Minikube** cluster. It intentionally injects controlled Kubernetes bugs and
performance problems into an `sre-lab` namespace, then uses a **LangGraph** maker/checker/judge
loop driven by a **local LLM (Ollama)** to repair them safely.

> **100% local.** No cloud APIs. No paid model APIs. No external production systems.
> The agent may only touch files inside this repo and Kubernetes resources inside `sre-lab`.

## Why

Built with **Spec-Driven Development** and **Loop Engineering**: reusable autonomous loops with
explicit triggers, tool contracts, safety gates, stopping rules, rollback, and named terminal
states — not one-off prompts. See [`specs/`](specs/) first.

## Target hardware (measured)

Apple M1 Pro · 16 GB unified memory · 8 cores (6P+2E) · Docker Desktop + Minikube v1.38 · Ollama.
Model tier is capped at **7–8B Q4** (`qwen3:8b`, fallback `qwen2.5:3b`) so Ollama + Minikube +
macOS coexist in 16 GB. Full report: [`specs/001-local-runtime-requirements.md`](specs/001-local-runtime-requirements.md).

## Quickstart

```bash
# 0. Inspect your Mac; confirm recommended sizing
make inspect

# 1. One-time local setup: pull models + start Minikube + create sre-lab
make setup            # == setup-ollama + setup-minikube
make venv             # python deps via uv

# 2. Deploy the healthy baseline lab, then break something
make lab-up
make inject SCENARIO=bad-probe

# 3. Run the SRE repair loop (dry-run shows the plan; apply fixes it)   [Phase 4+]
make run-agent SCENARIO=bad-probe MODE=dry-run
make run-agent SCENARIO=bad-probe MODE=apply-local-lab

# 4. Score with evals and read the postmortem                          [Phase 6+]
make eval
make report

# reset the lab any time
make reset
```

## Safety model (default = dry-run)

Modes: `dry-run` · `suggest-only` · `apply-local-lab`. Before any mutation the agent prints a
**planned-action block** (action / reason / resources / rollback / validation / risk). Auto-apply
is allowed **only** when: target ns is `sre-lab`, resource is part of the lab, a rollback command
is known, and a validation command is defined. Details: [`specs/007-safety-and-permissions.md`](specs/007-safety-and-permissions.md).

## Terminal states

`FIXED` · `IMPROVED` · `NO_ACTION_NEEDED` · `NEEDS_HUMAN` · `FAILED_SAFELY` · `ROLLED_BACK`.
The loop never runs forever: bounded by max iterations, max tool calls, and max elapsed time.

## Repository layout

```
specs/     Spec-driven design docs (000–009) — read these first
docs/      Architecture, LLM selection, loop engineering, safety narratives
src/sre_agent/   LangGraph app: graph, state, config, llm/ tools/ safety/ memory/ evals/ reports/
lab/       Minikube lab: healthy baseline + broken scenario overlays (kustomize)
evals/     Deterministic eval runner + scoring + cases
scripts/   inspect_mac / setup_ollama / setup_minikube / run_lab / inject_bug / reset_lab
```

## Status

Built in phases (see [`specs/009-roadmap.md`](specs/009-roadmap.md)).
**Delivered:** Phase 1 (specs) · Phase 2 (runtime scripts) · Phase 3 (lab + 3 scenarios).
**Next:** Phase 4 LangGraph dry-run loop → Phase 5 safe apply → Phase 6 evals → Phase 7 memory+reports.
