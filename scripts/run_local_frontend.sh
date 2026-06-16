#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/voice_agent_frontend"
if [[ ! -d node_modules ]]; then
  npm install
fi
exec npm run dev
