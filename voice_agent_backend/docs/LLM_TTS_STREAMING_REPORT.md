# LLM → TTS streaming analysis

**Date:** 2026-05-23  
**Scope:** `voice_agent_backend/agent` (paths in the task referred to `voice_agent/` — this repo uses `voice_agent_backend/agent/`).  
**Instrumentation:** Structured `PIPELINE_EVENT` lines in `logs/voice_agent_pipeline_latency.log` (no pipeline behavior changes).

## Executive summary

| Question | Answer (your current `.env`) |
|----------|------------------------------|
| Is LLM → TTS truly token-streamed into TTS? | **Yes** — LiveKit streams LLM text into `SynthesizeStream._input_ch`. |
| Does TTS HTTP start before `LLM_DONE`? | **Yes**, when `VOICE_AGENT_TTS_STREAM_PHRASES=true` (your setting): first sentence triggers `TTS_REQUEST_START` while the LLM may still be generating. |
| Does TTS HTTP start only after full LLM text? | **Only if** `VOICE_AGENT_TTS_STREAM_PHRASES=false` — then the blocking path buffers all tokens before one POST. |

## Providers checked

| Component | File | Role |
|-----------|------|------|
| LLM | `agent/pipeline/llm_openrouter.py` | OpenRouter via `livekit.plugins.openai.LLM` — standard LiveKit streaming LLM; no custom stream wrapper. |
| TTS (active) | `agent/pipeline/tts_supertonic.py` | Supertonic `POST /v1/tts` — full WAV per phrase or per reply. |
| TTS (alt) | `agent/pipeline/tts_multilingual_server.py` | `POST /tts_to_audio/` — same streaming/buffering pattern as Supertonic. |
| TTS (deprecated) | `agent/pipeline/tts_chatterbox.py` | Documents **buffer-until-end** in `SynthesizeStream`; not used when `TTS_PROVIDER=supertonic`. |
| STT | `agent/pipeline/stt_deepgram.py` | Deepgram only — not part of LLM→TTS path. |
| ElevenLabs | — | **Not present** in this repository. |

## Event log format

Each line looks like:

```text
PIPELINE_EVENT event=LLM_FIRST_TOKEN ts_perf=... delta_ms=... room=... turn_id=... ...
```

`delta_ms` is milliseconds since `USER_FINAL_TRANSCRIPT` for that turn.

| Event | Where emitted |
|-------|----------------|
| `USER_FINAL_TRANSCRIPT` | `entrypoint.py` — user STT final |
| `LLM_START` | `entrypoint.py` — agent state → `thinking` |
| `LLM_FIRST_TOKEN` | `tts_stream_hooks.py` (first token on TTS input stream) + `entrypoint.py` callback |
| `LLM_DONE` | `tts_supertonic.py` / `tts_multilingual_server.py` — LLM→TTS input channel closed |
| `TTS_REQUEST_START` | `pipeline_latency.py` — HTTP POST begins |
| `TTS_FIRST_AUDIO_FRAME` | `pipeline_latency.py` — first byte from HTTP response body |
| `TTS_DONE` | `pipeline_latency.py` — HTTP body complete |
| `AUDIO_PLAYOUT_START` | `pipeline_latency.py` — first chunk pushed to LiveKit audio emitter |

## Mode A — Phrase streaming (`VOICE_AGENT_TTS_STREAM_PHRASES=true`)

**Your `.env` enables this mode.**

### Code path

1. LiveKit LLM streams tokens into `SynthesizeStream` (`_input_ch`).
2. `SentenceTextChunker` accumulates until `.` / `!` / `?` (see `tts_text_chunker.py`).
3. Each completed phrase → `run_prefetched_phrase_playback()` → `POST /v1/tts` with **full phrase text** (not token streaming to TTS server).
4. `LLM_DONE` is logged when `_input_ch` ends (after last flush), which may be **after** the first `TTS_REQUEST_START`.

### Expected log order (proves streaming)

```text
USER_FINAL_TRANSCRIPT
LLM_START
LLM_FIRST_TOKEN
TTS_REQUEST_START          ← before LLM_DONE (first sentence ready)
TTS_FIRST_AUDIO_FRAME
TTS_DONE
AUDIO_PLAYOUT_START
... (more phrases) ...
LLM_DONE
```

If `TTS_REQUEST_START` appears **before** `LLM_DONE`, TTS is **not** waiting for the full LLM completion.

### Blocking code (none for first phrase)

There is **no** `await full_llm` before the first TTS call. Blocking is only **per phrase**: HTTP waits for the **entire phrase** WAV, not the entire reply.

Relevant code:

```279:318:voice_agent_backend/agent/pipeline/tts_supertonic.py
    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        ...
            if _stream_phrases_enabled():
                await self._run_phrase_stream(output_emitter)
                return
```

```218:236:voice_agent_backend/agent/pipeline/tts_supertonic.py
    async def _iter_phrases(self):
        ...
        async for data in self._input_ch:
            ...
        # LLM_DONE logged here after input channel drained
```

## Mode B — Buffered reply (`VOICE_AGENT_TTS_STREAM_PHRASES=false`)

### Code path (explicit block)

1. Buffer **all** LLM tokens in `parts: list[str]` until `_input_ch` closes.
2. Log `LLM_DONE`.
3. Join → `full_text`.
4. Single `TTS_REQUEST_START` + one `POST /v1/tts` with complete text (`mode=single_post_after_llm_buffered`).

### Expected log order

```text
USER_FINAL_TRANSCRIPT
LLM_START
LLM_FIRST_TOKEN
LLM_DONE
TTS_REQUEST_START          ← always after LLM_DONE
TTS_FIRST_AUDIO_FRAME
TTS_DONE
AUDIO_PLAYOUT_START
```

### Blocking code (exact)

```294:318:voice_agent_backend/agent/pipeline/tts_supertonic.py
            parts: list[str] = []
            async for data in self._input_ch:
                ...
                parts.append(token)
            ... LLM_DONE ...
            full_text = "".join(parts).strip()
            ...
            chunks, timing = await _fetch_audio_chunks(...)  # TTS only here
```

Chatterbox deprecated wrapper documents the same pattern:

```221:226:voice_agent_backend/agent/pipeline/tts_chatterbox.py
    One assistant reply → one POST /tts on the call's keep-alive connection.
    LLM tokens are buffered until the stream ends, then sent in a single request
```

## HTTP TTS: stream vs synthesize

| Layer | Streaming? |
|-------|------------|
| LiveKit LLM → `SynthesizeStream` | Yes (token/async iter) |
| TTS provider HTTP API | **No** — each call sends **full text**, receives **full WAV** (optionally read in chunks from socket) |
| LiveKit → room speaker | Chunks via `output_emitter.push()` after WAV is received |

Supertonic uses `session.post()` + `resp.content.iter_any()` — that is **chunked HTTP download**, not incremental TTS synthesis from partial LLM text (except phrase-by-phrase mode).

## UI / transcript streaming (separate path)

`voice_agent.py` `transcription_node` streams **assistant text to the data channel** for the UI. That path does **not** drive TTS timing; TTS is fed by the LiveKit agent pipeline separately.

## How to verify on a live call

1. Restart worker: `python -m agent dev`
2. Place one user utterance.
3. Open `voice_agent_backend/logs/voice_agent_pipeline_latency.log`.
4. For each `turn_id`, compare timestamps:
   - `TTS_REQUEST_START` vs `LLM_DONE`
   - `LLM_FIRST_TOKEN` vs `TTS_REQUEST_START`

## Conclusion

- **LLM is streamed** into the agent session by LiveKit.
- **TTS is phrase-streamed or fully buffered** depending on `VOICE_AGENT_TTS_STREAM_PHRASES`.
- With your current config (`true`), **TTS should start before `LLM_DONE`** for multi-sentence replies; logs will prove it.
- **No ElevenLabs**; **Deepgram** is STT-only; **OpenRouter** LLM has no custom buffering layer in this repo.
