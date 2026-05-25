"""LiveKit room audio capture tuning for low capture latency."""

from __future__ import annotations

import os

from livekit.agents.voice.room_io.types import AudioInputOptions


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def resolve_audio_input_options(*, sample_rate: int) -> AudioInputOptions:
    """
    Smaller frame_size_ms reduces mic→STT buffering (default 20ms vs SDK 50ms).

    pre_connect_audio stays enabled so frames can flow before AgentSession.start
    completes.
    """
    frame_ms = max(10, min(100, _env_int("VOICE_AGENT_AUDIO_FRAME_SIZE_MS", 20)))
    return AudioInputOptions(
        sample_rate=sample_rate,
        num_channels=1,
        frame_size_ms=frame_ms,
        auto_gain_control=_env_bool("VOICE_AGENT_AUDIO_AUTO_GAIN", True),
        pre_connect_audio=_env_bool("VOICE_AGENT_AUDIO_PRE_CONNECT", True),
        pre_connect_audio_timeout=_env_float(
            "VOICE_AGENT_AUDIO_PRE_CONNECT_TIMEOUT", 3.0
        ),
    )
