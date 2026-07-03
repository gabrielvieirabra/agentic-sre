#!/usr/bin/env bash
# Inject a broken scenario into sre-lab via its kustomize overlay.
# Usage: scripts/inject_bug.sh <scenario>   (aliases accepted; see _lab_common.sh)
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lab_common.sh"

[[ $# -ge 1 ]] || { echo "Usage: inject_bug.sh <scenario>"; exit 1; }
KEY="$(resolve_scenario "$1")"
DIR="$ROOT/lab/scenarios/$KEY"

# Special case: two consecutive bad deploys so a rollback lands on a still-bad
# revision -> the on-call loop cannot auto-recover and escalates to NEEDS_HUMAN.
if [[ "$KEY" == "bad-deploy-unrecoverable" ]]; then
  kubectl --context "$CTX" get deploy web -n "$NS" >/dev/null 2>&1 || \
    kubectl --context "$CTX" apply -k "$ROOT/lab/manifests/base" >/dev/null
  echo "Injecting scenario: $KEY (two bad revisions)"
  kc set image deploy/web web=nginx:1.0-bad-a >/dev/null
  kc rollout status deploy/web --timeout=15s >/dev/null 2>&1 || true
  kc set image deploy/web web=nginx:2.0-bad-b >/dev/null
  echo "Two bad revisions created; a rollback reverts to the still-broken v1 -> expect NEEDS_HUMAN."
  echo "Observe with: kubectl -n $NS get pods -l app=web"
  exit 0
fi

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
