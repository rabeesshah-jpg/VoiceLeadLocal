# RunPod + LiveKit Voice Agent Architecture

## Architecture diagram

```
┌─────────────────────┐         ┌──────────────────────────┐
│  React (Vercel /    │  HTTP   │  Django API (local /     │
│  local Vite)        ├────────►│  CPU cloud)              │
└─────────────────────┘         │  - sessions, tokens      │
                                │  - LiveKit room dispatch │
                                └────────────┬─────────────┘
                                             │
                    ┌────────────────────────┼────────────────────────┐
                    │                        │                        │
                    ▼                        ▼                        ▼
           ┌────────────────┐     ┌──────────────────┐    ┌─────────────────┐
           │ LiveKit Cloud  │     │ Cloud Postgres   │    │ (no TTS/STT/LLM │
           │ WebRTC rooms   │     │ DATABASE_URL     │    │  on Django)     │
           └───────┬────────┘     └────────▲─────────┘    └─────────────────┘
                   │                       │
                   │ agent job             │ call sessions / events
                   ▼                       │
           ┌───────────────────────────────────────────────┐
           │ RunPod 4090 instance                          │
           │  ┌─────────────────┐  ┌──────────────────┐  │
           │  │ TTS server GPU  │  │ LiveKit worker   │  │
           │  │ CUDA / ONNX     │◄─┤ CPU-only         │  │
           │  │ :7788 localhost │  │ Deepgram API     │  │
           │  │ (voiceLead-TTS) │  │ OpenRouter API   │  │
           │  └─────────────────┘  │ python -m agent  │  │
           │                       └──────────────────┘  │
           └───────────────────────────────────────────────┘
```

Realtime audio never passes through Django. The worker calls TTS only via `http://127.0.0.1:7788`.

### GPU / CPU split on RunPod

| Process | GPU | STT | LLM | TTS |
|---------|-----|-----|-----|-----|
| TTS server (`TTS/voiceLead-TTS`) | **Yes** — CUDA/ONNX | — | — | serves `:7788` |
| LiveKit worker | **No** — `CUDA_VISIBLE_DEVICES=""` for this process only | Deepgram (remote API) | OpenRouter (remote API) | HTTP `127.0.0.1:7788` |

Do **not** export `CUDA_VISIBLE_DEVICES=""` globally on the pod (that would disable GPU for TTS). Use `./scripts/run_runpod_worker.sh`, which sets it only for the worker child process.

Worker startup logs include: `worker_cpu_only`, `cuda_visible_devices`, `deepgram_remote_client`, `openrouter_remote_client`, `tts_health_check=pass|fail`.

## What runs where

| Component | Where | Notes |
|-----------|-------|-------|
| React frontend | Local now, Vercel later | Talks only to Django (`VITE_API_BASE_URL`) |
| Django API | Local now, CPU cloud later | LiveKit tokens/rooms, session DB |
| Cloud Postgres | Managed cloud | `DATABASE_URL` shared by Django + worker |
| LiveKit Cloud | LiveKit | WebRTC transport |
| TTS server | RunPod 4090 | `TTS/voiceLead-TTS` — external repo, do not modify |
| LiveKit worker | RunPod 4090 | CPU-only; `./scripts/run_runpod_worker.sh` |

## Environment files

| File | Used by |
|------|---------|
| `voice_agent_backend/.env.example` | Django API (local/cloud) |
| `voice_agent_backend/.env.runpod.example` | LiveKit worker on RunPod |
| `voice_agent_frontend/.env.example` | React frontend |

### Django API (local or cloud)

```env
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DB_NAME   # omit for local SQLite
LIVEKIT_URL=wss://your-livekit-cloud-url
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret
DJANGO_SECRET_KEY=change-me
DEBUG=true
ALLOWED_HOSTS=localhost,127.0.0.1
```

Do **not** set `TTS_BASE_URL` on cloud Django unless you intentionally run voice-profile admin against RunPod TTS.

### RunPod worker

```env
WORKER_ENV=runpod
WORKER_CPU_ONLY=true
DATABASE_URL=postgresql://USER:PASSWORD@HOST:5432/DB_NAME
LIVEKIT_URL=wss://your-livekit-cloud-url
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret
STT_PROVIDER=deepgram
DEEPGRAM_API_KEY=your_deepgram_key
OPENROUTER_API_KEY=your_openrouter_key
OPENROUTER_MODEL=openai/gpt-4o-mini
TTS_BASE_URL=http://127.0.0.1:7788
TTS_HEALTH_URL=http://127.0.0.1:7788/v1/health
TTS_VOICE=M1
```

### React frontend

```env
VITE_API_BASE_URL=http://127.0.0.1:8001
VITE_VOICE_AGENT_DEV_API_KEY=dev-local-key
```

## Start commands

### Local development

**1. Django backend**

```bash
cd voice_agent_backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill LIVEKIT_* only for API-only mode
python manage.py migrate
python manage.py runserver 8001
```

Or: `./scripts/run_local_backend.sh`

**2. React frontend**

```bash
cd voice_agent_frontend
npm install
cp .env.example .env
npm run dev
```

Or: `./scripts/run_local_frontend.sh`

**3. Start a call**

Open the frontend → it calls `POST /api/calls/start/` on Django → user joins LiveKit room with the returned token.

For local end-to-end voice, also run a worker (can be on the same machine with a shared `.env` that includes worker keys).

### RunPod

**1. Start TTS** (from the external `TTS/voiceLead-TTS` repo — use its existing script):

```bash
cd TTS/voiceLead-TTS
./scripts/start_tts_server.sh
```

**2. Verify TTS health**

```bash
./scripts/check_runpod_tts.sh
# or: curl -s http://127.0.0.1:7788/v1/health
```

**3. Start LiveKit worker**

```bash
./scripts/run_runpod_worker.sh
```

The script sets `CUDA_VISIBLE_DEVICES=""` and `STT_PROVIDER=deepgram` for the worker process only.

Manual equivalent:

```bash
cd voice_agent_backend
cp .env.runpod.example .env
source .venv/bin/activate
CUDA_VISIBLE_DEVICES="" WORKER_CPU_ONLY=true STT_PROVIDER=deepgram python -m agent start
```

Worker startup logs include `worker_cpu_only=true`, `cuda_visible_devices`, `deepgram_remote_client`, `openrouter_remote_client`, and `tts_health_check=pass|fail`.

## Verification checklist

- [ ] `curl -s http://127.0.0.1:7788/v1/health` returns JSON on RunPod
- [ ] TTS logs show `CUDAExecutionProvider` (GPU in use)
- [ ] Worker log shows `worker_cpu_only=True` and `cuda_visible_devices=` (empty)
- [ ] Worker log shows `deepgram_remote_client=True` and `openrouter_remote_client=True`
- [ ] Worker log shows `tts_base_url=http://127.0.0.1:7788` and `tts_health_check=pass`
- [ ] Django `GET /api/health/` shows `livekit_configured=true`
- [ ] `POST /api/calls/start/` returns `participant_token` and `room_name`
- [ ] Worker registers in LiveKit Cloud as `voice-agent` (or `VOICE_AGENT_NAME`)
- [ ] Voice call: user speech → agent audio in browser
- [ ] Django and worker both use the same `DATABASE_URL` in production

## Common mistakes

| Mistake | Fix |
|---------|-----|
| `CUDA_VISIBLE_DEVICES=""` in pod-wide `.bashrc` | Set only in `run_runpod_worker.sh` (worker process) |
| Worker using `faster_whisper` / local STT on RunPod | Use `STT_PROVIDER=deepgram` (enforced by worker) |
| Setting `TTS_BASE_URL` to RunPod public IP in worker | Use `http://127.0.0.1:7788` on the pod |
| Expecting Django to synthesize speech | TTS is worker-only |
| Frontend calling TTS directly | Only call Django API |
| Worker using SQLite while Django uses Postgres | Set `DATABASE_URL` on both |
| Forgetting to start TTS before worker | Run health check first |
| `python -m agent dev` in production on RunPod | Use `python -m agent start` |
| Missing LiveKit agent registration | Register worker name in LiveKit Cloud dashboard |

## Expected start flow

**Local**

1. Start Django backend.
2. Start React frontend.
3. React calls Django to create LiveKit session and token.
4. (Optional) Start local worker for realtime voice.

**RunPod**

1. Start TTS server from `TTS/voiceLead-TTS`.
2. Verify `curl http://127.0.0.1:7788/v1/health`.
3. Start LiveKit worker with `WORKER_ENV=runpod`.
4. Worker connects to LiveKit Cloud.
5. Worker calls TTS through localhost only.
