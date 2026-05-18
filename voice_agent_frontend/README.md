# Voice Agent Frontend

Minimal React + Vite client for the LiveKit voice agent demo.

## Setup

```bash
cd voice_agent_frontend
npm install
cp .env.example .env
npm run dev
```

Open http://localhost:5173 — ensure the API is running on port 8001.

## Environment

| Variable | Description |
|----------|-------------|
| `VITE_API_BASE_URL` | Django API base URL |
| `VITE_VOICE_AGENT_DEV_API_KEY` | Must match backend `VOICE_AGENT_DEV_API_KEY` |

No LiveKit or provider secrets belong in this app.
