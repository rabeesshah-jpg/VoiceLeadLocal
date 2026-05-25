"""Pre-open Deepgram streaming WebSocket before the caller speaks."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from livekit import rtc
from livekit.agents import stt
from livekit.plugins import deepgram

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


async def warmup_deepgram_stt(dg_stt: deepgram.STT) -> dict[str, Any]:
    """
    Open a short-lived Deepgram stream (WebSocket handshake + silence frame).

    Warms DNS/TLS and the shared aiohttp pool so the first user utterance avoids
    cold-connect latency.
    """
    if not stt_warmup_enabled():
        return {"ok": True, "skipped": True}

    sample_rate = _env_int("VOICE_AGENT_STT_SAMPLE_RATE", 16000)
    silence_ms = _env_int("VOICE_AGENT_STT_WARMUP_SILENCE_MS", 80)
    timeout_s = _env_int("VOICE_AGENT_STT_WARMUP_TIMEOUT_MS", 2500) / 1000.0

    t0 = time.perf_counter()
    stream = dg_stt.stream()
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
                    ):
                        break
        except TimeoutError:
            logger.warning(
                "deepgram_stt_warmup timed out after %.0fms (continuing)",
                timeout_s * 1000,
            )

        handshake_ms = int((time.perf_counter() - t0) * 1000)
        logger.info(
            "deepgram_stt_warmup ok handshake_ms=%s sample_rate=%s silence_ms=%s",
            handshake_ms,
            sample_rate,
            silence_ms,
        )
        return {"ok": True, "handshake_ms": handshake_ms, "sample_rate": sample_rate}
    except Exception as exc:
        logger.warning("deepgram_stt_warmup failed: %s", exc)
        return {"ok": False, "error": str(exc)[:200]}
    finally:
        await stream.aclose()
