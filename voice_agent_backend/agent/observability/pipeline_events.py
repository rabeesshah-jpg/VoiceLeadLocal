"""
Structured pipeline event timestamps (LLM ↔ TTS streaming analysis).

Logs to logs/voice_agent_pipeline_latency.log alongside TurnPipelineTracker summaries.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger("agent.observability.pipeline_events")

# Monotonic anchor per turn for delta_ms in logs.
_turn_t0: dict[str, float] = {}


def _turn_key(room: str, turn_id: str) -> str:
    return f"{room}:{turn_id}" if room and turn_id else turn_id or room or "unknown"


def clear_turn_anchor(*, room: str = "", turn_id: str = "") -> None:
    key = _turn_key(room, turn_id)
    if key:
        _turn_t0.pop(key, None)


def log_pipeline_event(
    event: str,
    *,
    room: str = "",
    turn_id: str = "",
    **fields: Any,
) -> None:
    """Emit one PIPELINE_EVENT line (no behavior change to the voice pipeline)."""
    now = time.perf_counter()
    key = _turn_key(room, turn_id)
    if event == "USER_FINAL_TRANSCRIPT" and key:
        _turn_t0[key] = now
    t0 = _turn_t0.get(key)
    delta_ms = int((now - t0) * 1000) if t0 is not None else None

    parts = [
        f"event={event}",
        f"ts_perf={now:.6f}",
    ]
    if delta_ms is not None:
        parts.append(f"delta_ms={delta_ms}")
    if room:
        parts.append(f"room={room}")
    if turn_id:
        parts.append(f"turn_id={turn_id}")
    for k, v in fields.items():
        if v is None:
            continue
        parts.append(f"{k}={v}")

    logger.info("PIPELINE_EVENT %s", " ".join(parts))


def tts_stream_mode_label() -> str:
    import os

    if os.environ.get("VOICE_AGENT_TTS_STREAM_PHRASES", "false").lower() == "true":
        return "phrase_post_per_sentence"
    return "single_post_after_llm_buffered"
