#!/usr/bin/env bash
# Start Minikube with recommended sizing and create the sre-lab namespace.
# Local only, docker driver. See specs/001 and specs/004.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1090
[[ -f "$ROOT/.env" ]] && set -a && source "$ROOT/.env" && set +a || true

NS="${SRE_NAMESPACE:-sre-lab}"
CTX="${SRE_KUBE_CONTEXT:-minikube}"
CPUS="${MINIKUBE_CPUS:-4}"
MEM="${MINIKUBE_MEMORY:-4096}"

command -v minikube >/dev/null 2>&1 || { echo "ERROR: minikube not installed"; exit 1; }
command -v kubectl  >/dev/null 2>&1 || { echo "ERROR: kubectl not installed"; exit 1; }
docker info >/dev/null 2>&1 || { echo "ERROR: Docker not running (start Docker Desktop)"; exit 1; }

if minikube status -p "$CTX" >/dev/null 2>&1; then
  echo "Minikube profile '$CTX' already running."
else
  echo "Starting Minikube (driver=docker cpus=$CPUS memory=${MEM}MB) ..."
  minikube start -p "$CTX" --driver=docker --cpus="$CPUS" --memory="$MEM"
fi

echo "Enabling metrics-server addon ..."
minikube addons enable metrics-server -p "$CTX" || echo "WARN: could not enable metrics-server"

echo "Using context '$CTX' and ensuring namespace '$NS' ..."
kubectl --context "$CTX" get ns "$NS" >/dev/null 2>&1 \
  || kubectl --context "$CTX" create namespace "$NS"

echo "Labeling namespace as the SRE lab (scope marker) ..."
kubectl --context "$CTX" label ns "$NS" sre-lab=true --overwrite >/dev/null

minikube status -p "$CTX"
echo "Ready: context=$CTX namespace=$NS"
echo "Next: make lab-up   (deploy the healthy baseline app)"
