# Local LLM Selection

Companion to [`specs/001-local-runtime-requirements.md`](../specs/001-local-runtime-requirements.md).

## The constraint that decides everything
This machine has **16 GB unified memory** (Apple M1 Pro). Ollama (host, Metal), Minikube (inside
the Docker VM), and macOS all share it. After the OS and a 4 GB Minikube VM, the model realistically
gets ~6–7 GB. That caps us at a **7–8B model at Q4** (~5 GB on disk/RAM).

## Policy applied
| RAM | Tier | This machine |
|---|---|---|
| 8 GB | 1.5B–3B | — |
| 16 GB | 3B–7B | **← here** |
| 24–32 GB | 7B–14B | — |
| 64 GB+ | larger | — |

We prioritize **tool-calling reliability** and **structured JSON** over raw benchmark scores,
because the loop lives or dies on the model emitting valid tool calls and parseable plans.

## Choice
- **Primary: `qwen3:8b`** — strong tool-calling + JSON adherence, fits the budget at Q4.
- **Fallback: `qwen2.5:3b`** — ~2 GB, much faster, leaves headroom when Minikube is under load or
  when latency matters more than reasoning depth. Auto-used on repeated parse failures.

## Providers considered
- **Ollama** ✅ already installed; native Metal; simple `pull`; OpenAI-compatible + native APIs.
- **LM Studio** — not installed; would also work via its OpenAI-compatible server.
- **llama.cpp** — not installed; lower-level, more setup.
- **vLLM** — ❌ not suitable on 16 GB Apple Silicon for this workload.

## Operational notes
- Set `keep_alive` so the model stays warm between loop iterations.
- Generation temperature low (`0.1`) for stable, parseable reasoning.
- Everything stays local — no tokens leave the machine.
