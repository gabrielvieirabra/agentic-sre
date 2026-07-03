#!/usr/bin/env bash
# Shared guards for lab scripts. Sourced, not executed.
# Enforces the safety scope: minikube context + sre-lab namespace only.

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1090
[[ -f "$ROOT/.env" ]] && set -a && source "$ROOT/.env" && set +a || true

CTX="${SRE_KUBE_CONTEXT:-minikube}"
NS="${SRE_NAMESPACE:-sre-lab}"

# Hard rails.
if [[ "$NS" != "sre-lab" ]]; then
  echo "REFUSING: namespace is locked to 'sre-lab' (got '$NS')." >&2; exit 2
fi
if [[ "$CTX" != "minikube" ]]; then
  echo "REFUSING: this project only operates on the 'minikube' context (got '$CTX')." >&2; exit 2
fi

command -v kubectl >/dev/null 2>&1 || { echo "ERROR: kubectl not installed" >&2; exit 1; }
kubectl config get-contexts "$CTX" >/dev/null 2>&1 || {
  echo "ERROR: kube context '$CTX' not found. Run 'make setup-minikube' first." >&2; exit 1; }

kc() { kubectl --context "$CTX" -n "$NS" "$@"; }

# Map friendly aliases -> canonical scenario directory keys.
resolve_scenario() {
  case "$1" in
    wrong-image|wrong-image-tag)                 echo "wrong-image-tag" ;;
    bad-probe|readiness|bad-readiness-probe)     echo "bad-readiness-probe" ;;
    service-selector|selector|service-selector-mismatch) echo "service-selector-mismatch" ;;
    *) echo "$1" ;;
  esac
}
