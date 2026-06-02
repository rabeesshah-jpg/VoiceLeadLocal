"""Per-turn latency tracking."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class TtsCallTiming:
    """One TTS HTTP request (Supertonic POST /v1/tts)."""

    text_preview: str
    chars: int
    ttfb_ms: int
    total_ms: int
    bytes_received: int

    def to_dict(self) -> dict:
        return {
            "text_preview": self.text_preview,
            "chars": self.chars,
            "ttfb_ms": self.ttfb_ms,
            "total_ms": self.total_ms,
            "bytes_received": self.bytes_received,
        }


@dataclass
class TurnLatency:
    turn_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    t_speech_end: float | None = None
    t_stt_final: float | None = None
    t_llm_dispatch: float | None = None
    t_llm_first_token: float | None = None
    t_audio_out: float | None = None
    tts_calls: list[TtsCallTiming] = field(default_factory=list)
    _start: float = field(default_factory=time.perf_counter)

    def mark_speech_end(self):
        self.t_speech_end = time.perf_counter()

    def mark_stt_final(self):
        self.t_stt_final = time.perf_counter()

    def mark_llm_dispatch(self):
        if self.t_llm_dispatch is None:
            self.t_llm_dispatch = time.perf_counter()

    def mark_llm_first_token(self):
        if self.t_llm_first_token is None:
            self.t_llm_first_token = time.perf_counter()

    def mark_audio_out(self):
        self.t_audio_out = time.perf_counter()

    def record_tts_call(self, timing: TtsCallTiming) -> None:
        self.tts_calls.append(timing)

    @property
    def tts_ttfb_ms(self) -> int | None:
        """TTS HTTP: request → first audio byte (excludes LLM time)."""
        if not self.tts_calls:
            return None
        return self.tts_calls[0].ttfb_ms

    @property
    def tts_total_ms(self) -> int:
        return sum(c.total_ms for c in self.tts_calls)

    def llm_start_ms(self) -> int | None:
        """STT final → LLM dispatch (agent entered thinking / started generation)."""
        if self.t_stt_final and self.t_llm_dispatch:
            delta = self.t_llm_dispatch - self.t_stt_final
            if delta >= 0:
                return int(delta * 1000)
        return None

    def llm_first_token_ms(self) -> int | None:
        """STT final → first streamed token (detail metric)."""
        if self.t_stt_final and self.t_llm_first_token:
            delta = self.t_llm_first_token - self.t_stt_final
            if delta >= 0:
                return int(delta * 1000)
        if self.t_llm_dispatch and self.t_llm_first_token:
            delta = self.t_llm_first_token - self.t_llm_dispatch
            if delta >= 0:
                return int(delta * 1000)
        return None

    def to_payload(self, *, pipeline_summary: dict | None = None) -> dict:
        base = self._start

        def ms(ts: float | None) -> int | None:
            if ts is None:
                return None
            return int((ts - base) * 1000)

        stt_ms = None
        if self.t_speech_end and self.t_stt_final:
            stt_ms = int((self.t_stt_final - self.t_speech_end) * 1000)
        payload: dict = {
            "turn_id": self.turn_id,
            "stt_ms": stt_ms,
            "llm_start_ms": self.llm_start_ms(),
            "llm_first_token_ms": self.llm_first_token_ms(),
            "tts_ttfb_ms": self.tts_ttfb_ms,
            "tts_total_ms": self.tts_total_ms or None,
            "tts_call_count": len(self.tts_calls) or None,
            "total_ms": ms(self.t_audio_out),
        }
        if self.tts_calls:
            payload["tts_calls"] = [c.to_dict() for c in self.tts_calls]
        if pipeline_summary:
            for key in (
                "e2e_to_first_audio_ms",
                "stt_post_speech_ms",
                "stt_speech_end_to_final_ms",
                "stt_wall_ms",
                "stt_first_partial_ms",
                "stt_speech_duration_ms",
                "user_speech_duration_ms",
                "stt_total_ms",
                "stt_partial_count",
                "stt_final_transcript_length",
                "stt_rejected",
                "stt_rejection_reason",
                "deepgram_endpointing_ms",
                "stt_provider",
                "llm_total_ms",
                "llm_time_to_first_token_ms",
                "llm_start_after_stt_final_ms",
                "tts_http_total_ms",
                "tts_time_to_first_audio_ms",
                "audio_push_duration_ms",
                "playback_total_ms",
                "e2e_to_playback_complete_ms",
                "transcript_length",
                "assistant_response_length",
                "prompt_tokens",
                "completion_tokens",
                "total_tokens",
            ):
                val = pipeline_summary.get(key)
                if val is not None and payload.get(key) is None:
                    payload[key] = val
        return payload
