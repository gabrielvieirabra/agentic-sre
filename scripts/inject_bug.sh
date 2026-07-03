#!/usr/bin/env bash
# Inject a broken scenario into sre-lab via its kustomize overlay.
# Usage: scripts/inject_bug.sh <scenario>   (aliases accepted; see _lab_common.sh)
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lab_common.sh"

[[ $# -ge 1 ]] || { echo "Usage: inject_bug.sh <scenario>"; exit 1; }
KEY="$(resolve_scenario "$1")"
DIR="$ROOT/lab/scenarios/$KEY"

if [[ ! -f "$DIR/kustomization.yaml" ]]; then
  echo "ERROR: scenario '$KEY' is not wired (no kustomization.yaml in $DIR)." >&2
  echo "Available MVP scenarios: wrong-image-tag, bad-readiness-probe, service-selector-mismatch" >&2
  exit 1
fi

# Ensure baseline exists first so overlays patch a real deployment.
kubectl --context "$CTX" get deploy web -n "$NS" >/dev/null 2>&1 || {
  echo "Baseline not found; applying it first ..."
  kubectl --context "$CTX" apply -k "$ROOT/lab/manifests/base" >/dev/null
}

echo "Injecting scenario: $KEY"
kubectl --context "$CTX" apply -k "$DIR"
echo "--- expected symptom (see lab/scenarios/$KEY/README.md) ---"
sed -n '1,8p' "$DIR/README.md" 2>/dev/null || true
echo
echo "Observe with: kubectl -n $NS get pods,endpoints -l app=web"
