#!/usr/bin/env bash
set -euo pipefail
LOG="${1:-/home/srdev/Livekit-Agent/voice_agent_backend/logs/voice_agent_ui_telemetry.log}"
FILTER="${2:-}"
echo "Tailing UI telemetry: $LOG"
touch "$LOG"
if [[ -n "$FILTER" ]]; then
  tail -f "$LOG" | grep --line-buffered -E "$FILTER"
else
  tail -f "$LOG"
fi
