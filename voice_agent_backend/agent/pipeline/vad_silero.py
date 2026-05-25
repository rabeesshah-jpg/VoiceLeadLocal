"""Silero VAD tuning for faster speech-end detection."""

from __future__ import annotations

import os

from livekit.plugins import silero


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def resolve_vad_min_silence_s() -> float:
    """Seconds of silence before Silero marks speech ended (default 0.4, not Silero's 0.55)."""
    if os.environ.get("VOICE_AGENT_VAD_MIN_SILENCE_S"):
        return max(0.05, _env_float("VOICE_AGENT_VAD_MIN_SILENCE_S", 0.4))
    if os.environ.get("VAD_SILENCE_TIMEOUT_MS"):
        return max(0.05, _env_int("VAD_SILENCE_TIMEOUT_MS", 400) / 1000.0)
    return 0.4


def resolve_vad_min_speech_s() -> float:
    """Minimum voiced duration before a segment counts as speech."""
    return max(0.01, _env_float("VOICE_AGENT_VAD_MIN_SPEECH_S", 0.05))


def resolve_vad_activation_threshold() -> float:
    return min(1.0, max(0.0, _env_float("VOICE_AGENT_VAD_ACTIVATION_THRESHOLD", 0.5)))


def build_silero_vad() -> silero.VAD:
    return silero.VAD.load(
        min_silence_duration=resolve_vad_min_silence_s(),
        min_speech_duration=resolve_vad_min_speech_s(),
        activation_threshold=resolve_vad_activation_threshold(),
    )
