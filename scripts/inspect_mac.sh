#!/usr/bin/env bash
# Inspect Mac hardware and print recommended local-LLM + Minikube sizing.
# Read-only. Safe to run any time. See specs/001-local-runtime-requirements.md.
set -euo pipefail

bold() { printf "\033[1m%s\033[0m\n" "$1"; }
kv()   { printf "  %-22s %s\n" "$1" "$2"; }

bold "== Mac hardware report =="

os_ver=$(sw_vers -productVersion 2>/dev/null || echo "?")
arch=$(uname -m)
cpu_brand=$(sysctl -n machdep.cpu.brand_string 2>/dev/null || echo "Apple Silicon")
mem_bytes=$(sysctl -n hw.memsize 2>/dev/null || echo 0)
mem_gb=$(( mem_bytes / 1024 / 1024 / 1024 ))
ncpu=$(sysctl -n hw.ncpu 2>/dev/null || echo "?")
p_cores=$(sysctl -n hw.perflevel0.logicalcpu 2>/dev/null || echo "?")
e_cores=$(sysctl -n hw.perflevel1.logicalcpu 2>/dev/null || echo 0)

if [[ "$arch" == "arm64" ]]; then plat="Apple Silicon"; else plat="Intel"; fi

kv "macOS" "$os_ver"
kv "Architecture" "$arch ($plat)"
kv "CPU" "$cpu_brand"
kv "Cores" "$ncpu (P:$p_cores E:$e_cores)"
kv "RAM" "${mem_gb} GB"

bold "== Container runtime =="
if command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1; then
  dver=$(docker version --format '{{.Server.Version}}' 2>/dev/null || echo "?")
  dmem=$(docker info --format '{{.MemTotal}}' 2>/dev/null || echo 0)
  dmem_gb=$(( dmem / 1024 / 1024 / 1024 ))
  kv "Docker" "$dver (VM ~${dmem_gb} GiB)"
else
  kv "Docker" "not running"
fi
command -v colima >/dev/null 2>&1 && kv "Colima" "$(colima version 2>/dev/null | head -1)" || true

bold "== Kubernetes tooling =="
command -v minikube >/dev/null 2>&1 && kv "minikube" "$(minikube version --short 2>/dev/null || minikube version 2>/dev/null | head -1)" || kv "minikube" "MISSING"
command -v kubectl  >/dev/null 2>&1 && kv "kubectl" "$(kubectl version --client 2>/dev/null | awk -F': ' '/Client Version/{print $2}')" || kv "kubectl" "MISSING"

bold "== Local LLM provider =="
if command -v ollama >/dev/null 2>&1; then
  kv "ollama" "$(ollama --version 2>&1 | grep -o '[0-9][0-9.]*' | head -1 || echo installed)"
else
  kv "ollama" "MISSING (brew install ollama)"
fi
command -v lms >/dev/null 2>&1 && kv "LM Studio" "present" || true

bold "== Recommendation =="
# Model tier by RAM policy (specs/001).
if   (( mem_gb <= 8  )); then model="qwen2.5:1.5b / phi3:mini (1.5B-3B)"; mk_mem=2048; mk_cpu=2
elif (( mem_gb <= 16 )); then model="qwen3:8b  (fallback qwen2.5:3b)   [3B-7B/8B Q4]"; mk_mem=4096; mk_cpu=4
elif (( mem_gb <= 32 )); then model="qwen2.5:14b / qwen3:14b (7B-14B Q4)"; mk_mem=6144; mk_cpu=4
else                          model="qwen2.5:32b+ (optimize for latency)"; mk_mem=8192; mk_cpu=6
fi
kv "Local model" "$model"
kv "Minikube" "--driver=docker --cpus=${mk_cpu} --memory=${mk_mem}"

if [[ "${dmem_gb:-0}" -gt 0 && "${dmem_gb:-0}" -lt 10 ]]; then
  printf "  \033[33m! Docker VM is %s GiB; raise to >=10 GiB so Minikube (%s MB) + model fit.\033[0m\n" "$dmem_gb" "$mk_mem"
fi
echo
echo "Next: make setup   (pulls model + starts Minikube + creates sre-lab)"
