#!/usr/bin/env bash
# Reset the lab back to the healthy baseline (heals any injected scenario).
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lab_common.sh"

echo "Re-applying healthy baseline to $NS (heals injected faults) ..."
kubectl --context "$CTX" apply -k "$ROOT/lab/manifests/base"

# Overlays only modify existing fields, so re-applying base restores healthy values.
kc rollout status deploy/web --timeout=120s || true
kc get pods,svc,endpoints -l app=web
echo "Lab reset to healthy baseline."
