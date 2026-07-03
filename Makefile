# Local-first SRE Agentic Platform — task runner
# Everything here runs locally: no cloud APIs, no paid model APIs.

SHELL := /bin/bash
NAMESPACE ?= sre-lab
SCENARIO ?= wrong-image-tag
MODE ?= dry-run

.DEFAULT_GOAL := help

.PHONY: help inspect setup setup-ollama setup-minikube lab-up inject reset \
        run-agent eval report clean lint test venv

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

## ---- Phase 2: local runtime ----
inspect: ## Inspect Mac hardware and print recommended sizing
	bash scripts/inspect_mac.sh

setup: setup-ollama setup-minikube ## Set up Ollama models + Minikube cluster

setup-ollama: ## Start Ollama and pull local models
	bash scripts/setup_ollama.sh

setup-minikube: ## Start Minikube with recommended sizing + create sre-lab ns
	bash scripts/setup_minikube.sh

## ---- Phase 3: lab ----
lab-up: ## Deploy healthy baseline workloads into sre-lab
	bash scripts/run_lab.sh

inject: ## Inject a broken scenario. Usage: make inject SCENARIO=bad-probe
	bash scripts/inject_bug.sh $(SCENARIO)

reset: ## Reset the lab back to healthy baseline
	bash scripts/reset_lab.sh

chaos: ## Chaos generator: inject a controlled fault. Usage: make chaos SCENARIO=bad-readiness-probe
	uv run sre-agent chaos --scenario $(SCENARIO)

history: ## Show run history trend from local memory
	uv run sre-agent report --history

## ---- Phase 4+: agent / evals / reports (implemented in later chunks) ----
run-agent: ## Run the SRE repair loop. Usage: make run-agent SCENARIO=bad-probe MODE=dry-run
	uv run sre-agent run --scenario $(SCENARIO) --mode $(MODE)

oncall: ## On-call incident response. Usage: make oncall SCENARIO=bad-deploy ALERT=lab/alerts/bad-deploy.json MODE=apply-local-lab
	uv run sre-agent oncall --scenario $(SCENARIO) $(if $(ALERT),--alert $(ALERT),) --mode $(MODE)

eval: ## Run the eval suite and score results
	uv run python -m evals.runner

report: ## Show the latest run report
	uv run sre-agent report --latest

## ---- dev ----
venv: ## Create local virtualenv and install deps with uv
	uv venv && uv pip install -e ".[dev]"

lint: ## Lint with ruff
	uv run ruff check src evals

test: ## Run pytest
	uv run pytest -q

clean: ## Remove runtime artifacts
	rm -rf runs reports/*.md reports/*.json snapshots *.sqlite
