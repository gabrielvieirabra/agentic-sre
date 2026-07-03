# 001 — Local Runtime Requirements

## Problem
Model choice and cluster sizing must fit the actual machine. Over-provisioning either the LLM or
Minikube will thrash a 16 GB laptop and make the loop unusably slow or unstable.

## Measured hardware report (this machine)
| Property | Value |
|---|---|
| Chip / arch | Apple M1 Pro / `arm64` |
| Cores | 8 (6 performance + 2 efficiency) |
| RAM | 16 GB unified memory |
| Container runtime | Docker Desktop 29.5.3 (VM capped at **7.75 GiB / 8 CPUs**); Colima 0.10.3 present |
| Kubernetes | Minikube v1.38.1, kubectl v1.36.2, kustomize v5.8.1 |
| Local LLM provider | Ollama 0.30.10 (daemon not running at inspect time) |
| Other providers | LM Studio / llama.cpp / vLLM: **not installed** |

**Memory budget reality:** Ollama (host, Metal) + Minikube (inside Docker VM) + macOS must
coexist in 16 GB. After the OS and the Docker VM, the model realistically gets ~6–7 GB.

## Goals
- Pick a local provider + model that is reliable at **tool-calling and structured JSON**.
- Recommend Minikube CPU/memory that leaves headroom for the model.
- Make the runtime reproducible via scripts (`scripts/inspect_mac.sh`, `setup_ollama.sh`, `setup_minikube.sh`).

## Non-goals
- No GPU cloud, no remote inference. No vLLM on this hardware (insufficient headroom).

## Requirements
- **Provider:** Ollama (already installed; native Metal; OpenAI-compatible + native APIs).
- **Model tier policy:** 16 GB → 3B–7B quantized. Ceiling here is **7–8B Q4** (~5 GB).
  - **Primary:** `qwen3:8b` (strong tool-calling + JSON).
  - **Fallback:** `qwen2.5:3b` (~2 GB) when Minikube is heavy or latency matters.
- **Minikube sizing:** `--driver=docker --cpus=4 --memory=4096`, addon `metrics-server`.
- **Docker Desktop:** recommend raising the VM to **≥10 GiB** (currently 7.75) so Minikube
  4 GB + system daemons have headroom.
- **Namespace:** all lab resources in `sre-lab`; context locked to `minikube`.

## Acceptance criteria
- `make inspect` prints the hardware report + the recommended model and Minikube sizing.
- `make setup-ollama` pulls the primary model and passes a JSON-output smoke test.
- `make setup-minikube` yields `minikube status = Running` and an existing `sre-lab` namespace.
- Running the smallest scenario + the agent uses < ~14 GB peak (leaves OS headroom).

## Risks
- Docker VM at 7.75 GiB may OOM Minikube under load → surfaced in `inspect` as a warning.
- `qwen3:8b` cold-start latency; mitigate with `keep_alive` and the 3B fallback.
- Model tag availability drift on Ollama registry → scripts verify pull success.

## Open questions
- Auto-detect and offer Colima if Docker VM is too small? (Deferred to roadmap.)

## Test strategy
- Smoke test: prompt the model for a fixed JSON schema and assert it parses.
- Resource check: capture `docker stats` / `kubectl top` before+after a scenario run.
