"""Pre-open STT streaming connection before the caller speaks."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

import aiohttp
from livekit import rtc
from livekit.agents import stt
from livekit.plugins import deepgram

from agent.pipeline.stt_config import (
    faster_whisper_timeout_seconds,
    is_deepgram_provider,
    is_faster_whisper_provider,
    resolve_faster_whisper_ws_url,
)

logger = logging.getLogger("agent.pipeline.stt_warmup")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def stt_warmup_enabled() -> bool:
    return not _env_bool("VOICE_AGENT_SKIP_STT_WARMUP", False)


async def _warmup_deepgram_stream(stt_instance: deepgram.STT, *, label: str) -> dict[str, Any]:
    sample_rate = _env_int("VOICE_AGENT_STT_SAMPLE_RATE", 16000)
    silence_ms = _env_int("VOICE_AGENT_STT_WARMUP_SILENCE_MS", 80)
    timeout_s = _env_int("VOICE_AGENT_STT_WARMUP_TIMEOUT_MS", 2500) / 1000.0

    t0 = time.perf_counter()
    stream = stt_instance.stream()
    try:
        samples = max(1, int(sample_rate * silence_ms / 1000))
        frame = rtc.AudioFrame(
            b"\x00\x00" * samples,
            sample_rate=sample_rate,
            num_channels=1,
            samples_per_channel=samples,
        )
        stream.push_frame(frame)
        stream.flush()
        stream.end_input()

        try:
            async with asyncio.timeout(timeout_s):
                async for event in stream:
                    if event.type in (
                        stt.SpeechEventType.START_OF_SPEECH,
                        stt.SpeechEventType.INTERIM_TRANSCRIPT,
                        stt.SpeechEventType.FINAL_TRANSCRIPT,
                        stt.SpeechEventType.RECOGNITION_USAGE,
                        stt.SpeechEventType.END_OF_SPEECH,
                    ):
                        break
        except TimeoutError:
            logger.warning("%s timed out after %.0fms (continuing)", label, timeout_s * 1000)

        handshake_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "%s ok handshake_ms=%s sample_rate=%s silence_ms=%s",
            label,
            handshake_ms,
            sample_rate,
            silence_ms,
        )
        return {"ok": True, "handshake_ms": handshake_ms, "sample_rate": sample_rate}
    except Exception as exc:
        logger.warning("%s failed: %s", label, exc)
        return {"ok": False, "error": str(exc)[:200]}
    finally:
        await stream.aclose()


async def _warmup_whisperlivekit_connect(
    http_session: aiohttp.ClientSession,
    *,
    language: str,
    label: str,
) -> dict[str, Any]:
    """
    Open WS, read server config, close — without starting a SpeechStream.

    A full STT stream during warmup races the live call stream and can make the
    server drop the first connection mid-receive.
    """
    timeout_s = _env_int("VOICE_AGENT_STT_WARMUP_TIMEOUT_MS", 2500) / 1000.0
    ws_url = resolve_faster_whisper_ws_url(language)
    t0 = time.perf_counter()
    ws: aiohttp.ClientWebSocketResponse | None = None
    try:
        ws = await asyncio.wait_for(
            http_session.ws_connect(
                ws_url,
                timeout=aiohttp.ClientTimeout(total=faster_whisper_timeout_seconds()),
                heartbeat=30.0,
                autoping=True,
            ),
            timeout=timeout_s,
        )
        msg = await asyncio.wait_for(ws.receive(), timeout=timeout_s)
        if msg.type != aiohttp.WSMsgType.TEXT:
            return {"ok": False, "error": f"unexpected ws message type {msg.type}"}
        data = json.loads(msg.data)
        if data.get("type") != "config":
            return {"ok": False, "error": f"expected config, got {data!r}"[:200]}
        handshake_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "%s ok handshake_ms=%s use_pcm=%s (connect-only)",
            label,
            handshake_ms,
            data.get("useAudioWorklet"),
        )
        return {
            "ok": True,
            "handshake_ms": handshake_ms,
            "mode": "connect_only",
            "use_pcm": data.get("useAudioWorklet"),
        }
    except TimeoutError:
        logger.warning("%s timed out after %.0fms (continuing)", label, timeout_s * 1000)
        return {"ok": True, "handshake_ms": int((time.perf_counter() - t0) * 1000), "mode": "connect_only"}
    except Exception as exc:
        logger.warning("%s failed: %s", label, exc)
        return {"ok": False, "error": str(exc)[:200]}
    finally:
        if ws is not None and not ws.closed:
            await ws.close()


async def warmup_faster_whisper_stt(
    fw_stt: stt.STT,
    *,
    language: str = "en",
    http_session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    if not stt_warmup_enabled():
        return {"ok": True, "skipped": True}
    session = http_session
    if session is None:
        from agent.pipeline.stt_http_pool import ensure_stt_http_session

        session = await ensure_stt_http_session()
    return await _warmup_whisperlivekit_connect(
        session,
        language=language,
        label="faster_whisper_stt_warmup",
    )


async def warmup_deepgram_stt(dg_stt: deepgram.STT) -> dict[str, Any]:
    if not stt_warmup_enabled():
        return {"ok": True, "skipped": True}
    return await _warmup_deepgram_stream(dg_stt, label="deepgram_stt_warmup")


async def warmup_stt(
    stt_instance: stt.STT,
    *,
    language: str = "en",
    http_session: aiohttp.ClientSession | None = None,
) -> dict[str, Any]:
    if not stt_warmup_enabled():
        return {"ok": True, "skipped": True}
    if is_faster_whisper_provider():
        return await warmup_faster_whisper_stt(
            stt_instance,
            language=language,
            http_session=http_session,
        )
    if is_deepgram_provider():
        return await warmup_deepgram_stt(stt_instance)  # type: ignore[arg-type]
    return await _warmup_whisperlivekit_connect(
        http_session or await _require_session(),
        language=language,
        label="stt_warmup",
    )


async def _require_session() -> aiohttp.ClientSession:
    from agent.pipeline.stt_http_pool import ensure_stt_http_session

    return await ensure_stt_http_session()
