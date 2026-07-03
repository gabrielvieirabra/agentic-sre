#!/usr/bin/env bash
# Start Ollama, pull local models, and smoke-test structured JSON output.
# Local only. See specs/001-local-runtime-requirements.md.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck disable=SC1090
[[ -f "$ROOT/.env" ]] && set -a && source "$ROOT/.env" && set +a || true

OLLAMA_BASE_URL="${OLLAMA_BASE_URL:-http://localhost:11434}"
MODEL="${SRE_MODEL:-qwen3:8b}"
FALLBACK="${SRE_MODEL_FALLBACK:-qwen2.5:3b}"

command -v ollama >/dev/null 2>&1 || { echo "ERROR: ollama not installed (brew install ollama)"; exit 1; }

# Start the daemon if not reachable.
if ! curl -sf -X GET "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1; then
  echo "Starting ollama daemon..."
  (ollama serve >/tmp/ollama.log 2>&1 &) || true
  for _ in $(seq 1 20); do
    curl -sf -X GET "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1 && break
    sleep 1
  done
fi
curl -sf -X GET "$OLLAMA_BASE_URL/api/tags" >/dev/null 2>&1 || { echo "ERROR: ollama not reachable at $OLLAMA_BASE_URL"; exit 1; }

for m in "$MODEL" "$FALLBACK"; do
  echo "Pulling $m ..."
  ollama pull "$m"
done

echo "Smoke test: structured JSON from $MODEL ..."
smoke=$(curl -sf -X POST "$OLLAMA_BASE_URL/api/generate" \
  -H 'Content-Type: application/json' \
  -d "{\"model\":\"$MODEL\",\"prompt\":\"Reply ONLY with compact JSON: {\\\"ok\\\":true,\\\"provider\\\":\\\"ollama\\\"}\",\"format\":\"json\",\"stream\":false}" \
  | python3 -c 'import sys,json; obj=json.loads(json.load(sys.stdin)["response"]); print("PASS" if obj.get("ok") else "WARN", obj)' 2>/dev/null || echo "WARN parse-failed")

if [[ "$smoke" == PASS* ]]; then
  echo "  $smoke"
else
  echo "  WARN: JSON smoke test unclear ($smoke). Model still usable; check manually."
fi
echo "Ollama ready: primary=$MODEL fallback=$FALLBACK"
