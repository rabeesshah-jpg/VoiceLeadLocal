"""Per-turn latency tracking."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field


@dataclass
class TurnLatency:
    turn_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    t_speech_end: float | None = None
    t_stt_final: float | None = None
    t_llm_first_token: float | None = None
    t_tts_first_byte: float | None = None
    t_audio_out: float | None = None
    _start: float = field(default_factory=time.perf_counter)

    def mark_speech_end(self):
        self.t_speech_end = time.perf_counter()

    def mark_stt_final(self):
        self.t_stt_final = time.perf_counter()

    def mark_llm_first_token(self):
        if self.t_llm_first_token is None:
            self.t_llm_first_token = time.perf_counter()

    def mark_tts_first_byte(self):
        if self.t_tts_first_byte is None:
            self.t_tts_first_byte = time.perf_counter()

    def mark_audio_out(self):
        self.t_audio_out = time.perf_counter()

    def to_payload(self) -> dict:
        base = self._start

        def ms(ts: float | None) -> int | None:
            if ts is None:
                return None
            return int((ts - base) * 1000)

        stt_ms = None
        if self.t_speech_end and self.t_stt_final:
            stt_ms = int((self.t_stt_final - self.t_speech_end) * 1000)
        llm_ms = None
        if self.t_stt_final and self.t_llm_first_token:
            llm_ms = int((self.t_llm_first_token - self.t_stt_final) * 1000)
        tts_ms = None
        if self.t_llm_first_token and self.t_tts_first_byte:
            tts_ms = int((self.t_tts_first_byte - self.t_llm_first_token) * 1000)

        return {
            "turn_id": self.turn_id,
            "stt_ms": stt_ms,
            "llm_first_token_ms": llm_ms,
            "tts_first_byte_ms": tts_ms,
            "total_ms": ms(self.t_audio_out),
        }
