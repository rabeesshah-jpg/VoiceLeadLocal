"""Promote stable interim STT text to PREFLIGHT for LiveKit preemptive LLM generation."""

from __future__ import annotations

import copy
import os
import time
from dataclasses import dataclass, field

from livekit.agents import stt


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


def partial_stt_preemptive_enabled() -> bool:
    if os.environ.get("VOICE_AGENT_PARTIAL_STT_PREEMPTIVE") is not None:
        return _env_bool("VOICE_AGENT_PARTIAL_STT_PREEMPTIVE", True)
    return _env_bool("VOICE_AGENT_PREEMPTIVE_GENERATION", True)


@dataclass
class PartialSttPreemptivePolicy:
    """Rate-limit interim → preflight promotion to avoid burning preemptive retries."""

    enabled: bool = True
    min_chars: int = 6
    min_growth_chars: int = 3
    min_interval_s: float = 0.15
    _last_preflight_text: str = field(default="", init=False, repr=False)
    _last_preflight_at: float = field(default=0.0, init=False, repr=False)

    @classmethod
    def from_env(cls) -> PartialSttPreemptivePolicy:
        return cls(
            enabled=partial_stt_preemptive_enabled(),
            min_chars=_env_int("VOICE_AGENT_PARTIAL_STT_MIN_CHARS", 6),
            min_growth_chars=_env_int("VOICE_AGENT_PARTIAL_STT_MIN_GROWTH_CHARS", 3),
            min_interval_s=_env_int("VOICE_AGENT_PARTIAL_STT_MIN_INTERVAL_MS", 150) / 1000.0,
        )

    def reset(self) -> None:
        self._last_preflight_text = ""
        self._last_preflight_at = 0.0

    def should_emit_preflight(self, text: str, *, now: float | None = None) -> bool:
        if not self.enabled:
            return False
        cleaned = text.strip()
        if len(cleaned) < self.min_chars:
            return False
        ts = now if now is not None else time.perf_counter()
        if self._last_preflight_text:
            if cleaned == self._last_preflight_text:
                return False
            if ts - self._last_preflight_at < self.min_interval_s:
                return False
            if cleaned.startswith(self._last_preflight_text):
                growth = len(cleaned) - len(self._last_preflight_text)
                if growth < self.min_growth_chars:
                    return False
        return True

    def mark_emitted(self, text: str, *, now: float | None = None) -> None:
        self._last_preflight_text = text.strip()
        self._last_preflight_at = now if now is not None else time.perf_counter()


def preflight_from_interim(event: stt.SpeechEvent) -> stt.SpeechEvent:
    """Build a PREFLIGHT transcript event from an interim Deepgram result."""
    return stt.SpeechEvent(
        type=stt.SpeechEventType.PREFLIGHT_TRANSCRIPT,
        request_id=event.request_id,
        alternatives=copy.deepcopy(event.alternatives),
        recognition_usage=event.recognition_usage,
        speech_start_time=event.speech_start_time,
    )
