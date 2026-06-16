#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/voice_agent_backend"

export WORKER_ENV="${WORKER_ENV:-runpod}"
export TTS_BASE_URL="${TTS_BASE_URL:-http://127.0.0.1:7788}"
export TTS_HEALTH_URL="${TTS_HEALTH_URL:-http://127.0.0.1:7788/v1/health}"

if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

# CPU-only for this worker process — does NOT affect TTS server (separate process).
export WORKER_CPU_ONLY=true
export CUDA_VISIBLE_DEVICES=""
export STT_PROVIDER=deepgram

if [[ -f .venv/bin/activate ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "Starting LiveKit worker (CPU-only)"
echo "  WORKER_ENV=$WORKER_ENV"
echo "  WORKER_CPU_ONLY=$WORKER_CPU_ONLY"
echo "  CUDA_VISIBLE_DEVICES='${CUDA_VISIBLE_DEVICES}'"
echo "  STT_PROVIDER=$STT_PROVIDER (remote Deepgram API)"
echo "  TTS_BASE_URL=$TTS_BASE_URL (local HTTP only)"
exec python -m agent start
