# Voice Agent Backend

Standalone Django API + LiveKit agent worker for ultra-low-latency voice conversations.

## Stack

- **LiveKit** — WebRTC room / audio transport
- **Deepgram** — streaming STT (server-side in worker)
- **OpenRouter** — `openai/gpt-4o-mini` streaming LLM
- **Multilingual TTS** — HTTP (`POST /tts_to_audio/`, `language` + `speaker_wav`, 24 kHz WAV). Set `TTS_PROVIDER=supertonic` for local Supertonic-3.

## Quick start (local)

```bash
cd voice_agent_backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill LIVEKIT_*, STT_PROVIDER + STT_WS_URL (or DEEPGRAM_*), OPENROUTER_*, TTS_BASE_URL
python manage.py migrate
python manage.py runserver 8001
```

In a second terminal:

```bash
cd voice_agent_backend && source .venv/bin/activate
python -m agent dev
```

## API

| Endpoint | Description |
|----------|-------------|
| `GET /api/health/` | Health + config status |
| `POST /api/calls/start/` | Create room + user token |
| `POST /api/calls/{id}/end/` | End session |
| `GET /api/calls/{id}/` | Session detail + metrics |

Dev auth header: `X-Voice-Agent-Dev-Key: <VOICE_AGENT_DEV_API_KEY>`

## Docker

From repo root:

```bash
docker compose -f docker-compose.voice-agent.yml up --build
```

## Logging

Each process **clears its log file on startup** and writes formatted step logs with **duration_ms** per component.

| Process | Log file |
|---------|----------|
| Django API (`runserver 8001`) | `logs/voice_agent_api.log` |
| Agent worker (`python -m agent dev`) | `logs/voice_agent_worker.log` |
| Pipeline latency (STT → LLM → TTS milestones) | `logs/voice_agent_pipeline_latency.log` |

Restart the API or worker to rotate logs (previous content is replaced, not appended).

Example block:

```
────────────────────────────────────────────────────────────────────────────────
  operation      START_CALL
  step           mint_livekit_token
  status         OK
  duration_ms    12.45
  request_id     a1b2c3d4
  call_id        ...
────────────────────────────────────────────────────────────────────────────────
```

## RunPod TTS smoke test

```bash
cd voice_agent_backend
source .venv/bin/activate
python scripts/test_runpod_tts.py

# Custom voice (use RunPod profile UUID, not vp_* engine name):
python scripts/test_runpod_tts.py --voice-id <uuid>
```

Required `.env` values:

```
TTS_BASE_URL=http://103.196.86.172:34679
TTS_MODEL=supertonic-3
TTS_LANG=en
TTS_TIMEOUT=60
TTS_CLONE_ENDPOINT=/v1/voices/clone
```

Recorded audio cloning requires `SUPERTONE_API_KEY` on the RunPod server (`supports_reference_audio_cloning=true` in capabilities).

## Tests

```bash
pytest tests/ -q
```

## Production notes

- Run **API** (Gunicorn) and **worker** (`python -m agent start`) as separate processes.
- Register the worker agent name `voice-agent` (or `VOICE_AGENT_NAME`) in LiveKit Cloud.
- Never expose provider API keys to the frontend.
