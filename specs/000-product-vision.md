# 000 — Product Vision

## Problem
Operating even a small Kubernetes cluster involves repetitive investigation and repair toil:
reading pod state, correlating events/logs, forming a hypothesis, applying a minimal fix,
validating, and writing it up. There is no safe, fully local sandbox to practice, evaluate, and
automate this loop without touching production or paying for cloud/model APIs.

## Goals
- A **local-first** SRE agentic platform operating against a local **Minikube** cluster.
- First capability: an **autonomous SRE repair loop** that detects, investigates, fixes,
  validates, and documents issues in the `sre-lab` namespace.
- Intentionally inject controlled bugs/perf problems, then repair or improve them.
- Everything runs locally: Python, LangGraph, a local LLM (Ollama), Minikube. No cloud/paid APIs.
- Engineering system, not a chatbot: specs → loop design → tool contracts → safety → evals →
  observability → measurable improvement.

## Non-goals
- No cloud provider, no managed K8s, no paid model API, no external production systems.
- Not a general chatbot or a general kubectl wrapper for arbitrary clusters.
- Not multi-tenant / not a hosted service. Single developer, single laptop.
- No mutation outside the repo and the `sre-lab` namespace on the local cluster.

## Requirements
- Detect abnormal cluster state and classify the incident.
- Form a hypothesis, collect evidence, propose the **smallest safe fix**.
- Apply the fix only under safety gates; validate; roll back if worse.
- Produce a postmortem report and store reusable lessons in local memory.
- Every run ends in a **named terminal state** and is fully reproducible.

## Acceptance criteria
- The 12 MVP acceptance criteria in [`009-roadmap.md`](009-roadmap.md) all pass.
- A new user can go from empty repo to a scored repair run using only `make` targets.
- No network call leaves the machine except pulling public container/model images.

## Risks
- Local memory pressure (16 GB) running Ollama + Minikube + macOS simultaneously.
- LLM tool-calling/JSON reliability on small local models.
- Cluster nondeterminism (image pulls, scheduling) making evals flaky.

## Open questions
- Should later phases support Colima as an alternative to Docker Desktop? (Deferred.)
- Do we want a TUI "incident game mode" beyond the CLI? (Creative backlog.)

## Test strategy
- Deterministic end-to-end: inject a known-broken scenario, run the loop, assert the terminal
  state and deterministic health checks (rollout status, HTTP 200, endpoints, ready count).
- Specs reviewed against the MVP gate before any agent code is written.
