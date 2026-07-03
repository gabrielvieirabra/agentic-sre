#!/usr/bin/env bash
# Deploy the healthy baseline app into sre-lab and wait for it to be ready.
set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_lab_common.sh"

kubectl --context "$CTX" get ns "$NS" >/dev/null 2>&1 || kubectl --context "$CTX" create ns "$NS"

echo "Applying healthy baseline to $NS ..."
kubectl --context "$CTX" apply -k "$ROOT/lab/manifests/base"
kubectl --context "$CTX" apply -k "$ROOT/lab/manifests/depsvc"

echo "Waiting for rollout ..."
kc rollout status deploy/web --timeout=120s || true
kc rollout status deploy/depsvc --timeout=120s || true
kc get pods,svc,endpoints
echo "Lab baseline is up. Break it with: make inject SCENARIO=bad-probe"
