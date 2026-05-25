"""One log per turn: LLM first token + TTS input→first server chunk."""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

from agent.observability.pipeline_events import log_pipeline_event
from config.step_log import log_block

logger = logging.getLogger("agent.observability.pipeline_latency")


@dataclass
class TurnPipelineTracker:
    room: str = ""
    turn_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    _logged: bool = False

    t_llm_dispatch: float | None = None
    t_llm_first_token: float | None = None
    t_tts_llm_input_first: float | None = None
    t_tts_first_server_chunk: float | None = None

    def reset_turn(self, *, room: str) -> None:
        self.room = room
        self.turn_id = str(uuid.uuid4())[:8]
        self._logged = False
        self.t_llm_dispatch = None
        self.t_llm_first_token = None
        self.t_tts_llm_input_first = None
        self.t_tts_first_server_chunk = None

    def _ms_between(self, start: float | None, end: float | None) -> int | None:
        if start is None or end is None:
            return None
        return int((end - start) * 1000)

    def mark_stt_final(self, *, transcript_preview: str = "") -> None:
        pass

    def mark_llm_dispatch(self) -> None:
        if self.t_llm_dispatch is None:
            self.t_llm_dispatch = time.perf_counter()

    def mark_llm_first_token(self, *, token_preview: str = "") -> None:
        if self.t_llm_first_token is None:
            self.t_llm_first_token = time.perf_counter()

    def mark_tts_llm_input_first(self, *, token_preview: str = "") -> None:
        if self.t_tts_llm_input_first is None:
            self.t_tts_llm_input_first = time.perf_counter()

    def mark_tts_http_start(self, *, text_chars: int, mode: str) -> None:
        log_pipeline_event(
            "TTS_REQUEST_START",
            room=self.room,
            turn_id=self.turn_id,
            text_chars=text_chars,
            tts_mode=mode,
        )

    def mark_tts_http_complete(self, *, timing_ms: int, bytes_received: int) -> None:
        log_pipeline_event(
            "TTS_DONE",
            room=self.room,
            turn_id=self.turn_id,
            timing_ms=timing_ms,
            bytes_received=bytes_received,
        )

    def mark_tts_first_playout_chunk(self, *, chunk_bytes: int) -> None:
        log_pipeline_event(
            "AUDIO_PLAYOUT_START",
            room=self.room,
            turn_id=self.turn_id,
            chunk_bytes=chunk_bytes,
        )

    def mark_tts_first_server_chunk(self) -> None:
        """First audio bytes returned from POST /v1/tts (not LiveKit playout)."""
        if self.t_tts_first_server_chunk is None:
            self.t_tts_first_server_chunk = time.perf_counter()
            log_pipeline_event(
                "TTS_FIRST_AUDIO_FRAME",
                room=self.room,
                turn_id=self.turn_id,
            )
            self.log_turn_once()

    def log_turn_once(self) -> None:
        if self._logged:
            return
        self._logged = True

        log_block(
            logger,
            logging.INFO,
            operation="PIPELINE_LATENCY",
            step="turn",
            status="OK",
            room=self.room,
            turn_id=self.turn_id,
            llm_first_token_ms=self._ms_between(
                self.t_llm_dispatch, self.t_llm_first_token
            ),
            tts_input_to_first_chunk_ms=self._ms_between(
                self.t_tts_llm_input_first, self.t_tts_first_server_chunk
            ),
        )

    def log_turn_summary(self) -> None:
        self.log_turn_once()
