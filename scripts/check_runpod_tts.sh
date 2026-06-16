#!/usr/bin/env bash
set -euo pipefail
URL="${TTS_HEALTH_URL:-http://127.0.0.1:7788/v1/health}"
echo "Checking TTS health: $URL"
curl -sf "$URL"
echo
