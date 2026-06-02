"""Per-turn pipeline performance timestamps and structured latency logs."""

from __future__ import annotations

import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from agent.observability.pipeline_events import clear_turn_anchor, log_pipeline_event
from config.step_log import log_block

logger = logging.getLogger("agent.observability.pipeline_latency")


def _deepgram_endpointing_ms() -> int | None:
    try:
        return int(os.environ.get("VOICE_AGENT_DEEPGRAM_ENDPOINTING_MS", "100"))
    except ValueError:
        return None


def _missing_reason_ms(
    value_ms: int | None,
    *,
    start: float | None,
    end: float | None,
    start_label: str = "speech_start",
    end_label: str = "speech_end",
) -> str | None:
    if value_ms is not None:
        return None
    if start is None:
        return f"{start_label}_not_seen"
    if end is None:
        return f"{end_label}_not_seen"
    return "interval_not_computable"


@dataclass
class TurnPipelineTracker:
    """Tracks one user utterance through STT → LLM → TTS → LiveKit playout."""

    room: str = ""
    turn_id: str = ""
    turn_seq: int = 0
    stt_provider: str = ""
    tts_provider: str = ""
    tts_voice: str = ""
    tts_model: str = ""
    llm_model: str = ""

    # Live STT clocks (current utterance; cleared when LLM phase starts).
    t_user_speech_start: float | None = None
    t_user_speech_end: float | None = None
    t_stt_processing_start: float | None = None
    t_stt_first_partial: float | None = None
    t_stt_final: float | None = None
    transcript_length: int = 0
    stt_partial_count: int = 0
    stt_final_text_preview: str = ""
    stt_rejected: bool = False
    stt_rejection_reason: str | None = None
    low_confidence_rejected: bool = False

    # Snapshot at USER_FINAL (survives reset_turn phase=llm for summaries).
    _snap_speech_start: float | None = None
    _snap_speech_end: float | None = None
    _snap_stt_processing_start: float | None = None
    _snap_stt_first_partial: float | None = None
    _snap_stt_final: float | None = None
    _snap_transcript_length: int = 0
    _snap_partial_count: int = 0
    _snap_final_text_preview: str = ""
    _snap_rejected: bool = False
    _snap_rejection_reason: str | None = None
    _snap_low_confidence_rejected: bool = False

    t_llm_phase_start: float | None = None
    t_llm_dispatch: float | None = None
    t_llm_first_token: float | None = None
    t_llm_complete: float | None = None
    t_tts_llm_input_first: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None

    t_tts_request_start: float | None = None
    t_tts_first_server_chunk: float | None = None
    t_tts_http_complete: float | None = None
    tts_http_total_ms: int = 0
    tts_bytes_received: int = 0

    t_playback_start: float | None = None
    t_playback_first_chunk: float | None = None
    t_playback_complete: float | None = None

    assistant_response_length: int = 0
    _logged: bool = False
    _stt_summary_logged: bool = False

    def configure_providers(
        self,
        *,
        stt_provider: str = "",
        tts_provider: str = "",
        tts_voice: str = "",
        tts_model: str = "",
        llm_model: str = "",
    ) -> None:
        if stt_provider:
            self.stt_provider = stt_provider
        if tts_provider:
            self.tts_provider = tts_provider
        if tts_voice:
            self.tts_voice = tts_voice
        if tts_model:
            self.tts_model = tts_model
        if llm_model:
            self.llm_model = llm_model

    def _clear_live_stt(self) -> None:
        self.t_user_speech_start = None
        self.t_user_speech_end = None
        self.t_stt_processing_start = None
        self.t_stt_first_partial = None
        self.t_stt_final = None
        self.transcript_length = 0
        self.stt_final_text_preview = ""

    def _clear_stt_snapshot(self) -> None:
        self._snap_speech_start = None
        self._snap_speech_end = None
        self._snap_stt_processing_start = None
        self._snap_stt_first_partial = None
        self._snap_stt_final = None
        self._snap_transcript_length = 0
        self._snap_partial_count = 0
        self._snap_final_text_preview = ""
        self._snap_rejected = False
        self._snap_rejection_reason = None
        self._snap_low_confidence_rejected = False

    def _snapshot_stt_phase(self) -> None:
        self._snap_speech_start = self.t_user_speech_start
        self._snap_speech_end = self.t_user_speech_end
        self._snap_stt_processing_start = self.t_stt_processing_start
        self._snap_stt_first_partial = self.t_stt_first_partial
        self._snap_stt_final = self.t_stt_final
        self._snap_transcript_length = self.transcript_length
        self._snap_partial_count = self.stt_partial_count
        self._snap_final_text_preview = self.stt_final_text_preview
        self._snap_rejected = self.stt_rejected
        self._snap_rejection_reason = self.stt_rejection_reason
        self._snap_low_confidence_rejected = self.low_confidence_rejected

    def begin_pipeline_turn(self, *, turn_seq: int) -> str:
        """Open a new pipeline turn_id for one user utterance (``turn_seq``)."""
        self.turn_seq = turn_seq
        self.turn_id = str(uuid.uuid4())[:8]
        self._logged = False
        self._stt_summary_logged = False
        self.stt_rejected = False
        self.stt_rejection_reason = None
        self.low_confidence_rejected = False
        self.stt_partial_count = 0
        # Preserve live STT clocks if VAD fired just before turn creation.
        if self.t_user_speech_start is None:
            self._clear_live_stt()
        log_pipeline_event(
            "PIPELINE_TURN_CREATED",
            room=self.room,
            turn_id=self.turn_id,
            turn_seq=turn_seq,
        )
        return self.turn_id

    def close_pipeline_turn(self) -> None:
        """Mark the current turn_id closed; do not reuse until ``begin_pipeline_turn``."""
        if not self.turn_id:
            return
        log_pipeline_event(
            "PIPELINE_TURN_CLOSED",
            room=self.room,
            turn_id=self.turn_id,
            turn_seq=self.turn_seq,
        )
        clear_turn_anchor(room=self.room, turn_id=self.turn_id)
        self._clear_live_stt()

    def reset_turn(self, *, room: str, phase: str = "full") -> None:
        """Reset tracker. ``phase='llm'`` snapshots STT then starts LLM-phase clocks."""
        self.room = room
        if phase == "full":
            self.turn_id = ""
            self.turn_seq = 0
            self.stt_rejected = False
            self.stt_rejection_reason = None
            self.low_confidence_rejected = False
        self._logged = False
        if phase == "llm":
            self._snapshot_stt_phase()
            self._clear_live_stt()
            self.t_llm_phase_start = time.perf_counter()
        else:
            self._clear_stt_snapshot()
            self._clear_live_stt()
            self.t_llm_phase_start = None

        self.t_llm_dispatch = None
        self.t_llm_first_token = None
        self.t_llm_complete = None
        self.t_tts_llm_input_first = None
        self.prompt_tokens = None
        self.completion_tokens = None
        self.total_tokens = None
        self.t_tts_request_start = None
        self.t_tts_first_server_chunk = None
        self.t_tts_http_complete = None
        self.tts_http_total_ms = 0
        self.tts_bytes_received = 0
        self.t_playback_start = None
        self.t_playback_first_chunk = None
        self.t_playback_complete = None
        self.assistant_response_length = 0

    def begin_user_utterance(self) -> None:
        """New VAD utterance after greeting — reset STT clocks only."""
        self._clear_live_stt()
        now = time.perf_counter()
        self.t_user_speech_start = now

    def end_greeting_phase(self) -> None:
        """Drop any STT/playback marks collected during the opening greeting."""
        self.reset_turn(room=self.room, phase="full")

    def _ms_between(self, start: float | None, end: float | None) -> int | None:
        if start is None or end is None:
            return None
        return int((end - start) * 1000)

    def _since_llm_phase_ms(self, ts: float | None) -> int | None:
        return self._ms_between(self.t_llm_phase_start, ts)

    def _since_utterance_ms(self, ts: float | None) -> int | None:
        return self._ms_between(self.t_user_speech_start, ts)

    def _log_perf(
        self,
        event: str,
        *,
        segment: str,
        segment_ms: int | None = None,
        **fields: Any,
    ) -> None:
        now = time.perf_counter()
        extra: dict[str, Any] = {
            "segment": segment,
            "stt_provider": self.stt_provider or None,
            "tts_provider": self.tts_provider or None,
            "tts_voice": self.tts_voice or None,
            "llm_model": self.llm_model or None,
        }
        if segment_ms is not None:
            extra["segment_ms"] = segment_ms
        if segment == "stt":
            since = self._since_utterance_ms(now)
            if since is not None:
                extra["since_utterance_ms"] = since
        elif self.t_llm_phase_start is not None:
            since = self._since_llm_phase_ms(now)
            if since is not None:
                extra["since_llm_phase_ms"] = since
        extra.update(fields)
        log_pipeline_event(
            event,
            room=self.room,
            turn_id=self.turn_id,
            turn_seq=self.turn_seq or None,
            **extra,
        )

    def mark_user_speech_start(self) -> None:
        now = time.perf_counter()
        if self.t_user_speech_start is None:
            self.begin_user_utterance()
            self._log_perf(
                "USER_SPEECH_START",
                segment="stt",
                segment_ms=0,
                user_speech_start_ts=now,
            )

    def mark_user_speech_end(self) -> None:
        now = time.perf_counter()
        if self.t_user_speech_end is None:
            self.t_user_speech_end = now
            self._log_perf(
                "USER_SPEECH_END",
                segment="stt",
                segment_ms=self._ms_between(self.t_user_speech_start, now),
                user_speech_duration_ms=self._ms_between(
                    self.t_user_speech_start, now
                ),
                user_speech_end_ts=now,
                stt_speech_duration_ms=self._ms_between(
                    self.t_user_speech_start, now
                ),
            )

    def record_stt_partial(self, *, text_preview: str = "") -> None:
        """Count each interim transcript; record time of first partial."""
        self.stt_partial_count += 1
        if self.t_stt_first_partial is None:
            self.mark_stt_first_partial(text_preview=text_preview)

    def mark_stt_processing_start(self) -> None:
        now = time.perf_counter()
        if self.t_stt_processing_start is None:
            self.t_stt_processing_start = now
            self._log_perf(
                "STT_PROCESSING_START",
                segment="stt",
                segment_ms=self._ms_between(self.t_user_speech_start, now),
            )

    def mark_stt_first_partial(self, *, text_preview: str = "") -> None:
        now = time.perf_counter()
        if self.t_stt_first_partial is None:
            self.t_stt_first_partial = now
            stt_first_partial_ms = self._ms_between(self.t_user_speech_start, now)
            self._log_perf(
                "STT_FIRST_PARTIAL",
                segment="stt",
                segment_ms=stt_first_partial_ms,
                stt_first_partial_ms=stt_first_partial_ms,
                stt_first_partial_ts=now,
                text_preview=text_preview[:80] if text_preview else None,
            )

    def mark_stt_final(self, *, transcript_preview: str = "") -> None:
        now = time.perf_counter()
        first = self.t_stt_final is None
        if first:
            self.t_stt_final = now
        if transcript_preview:
            preview = transcript_preview.strip()
            self.transcript_length = len(preview)
            self.stt_final_text_preview = preview[:80]
        if not first:
            return
        # User-perceived STT wait: after they stop speaking → final text ready.
        stt_speech_end_to_final_ms = self._ms_between(self.t_user_speech_end, now)
        if stt_speech_end_to_final_ms is None:
            stt_speech_end_to_final_ms = self._ms_between(
                self.t_stt_processing_start, now
            )
        stt_wall_ms = self._ms_between(self.t_user_speech_start, now)
        endpointing_ms = (
            _deepgram_endpointing_ms() if self.stt_provider == "deepgram" else None
        )
        self._log_perf(
            "STT_FINAL",
            segment="stt",
            segment_ms=stt_speech_end_to_final_ms,
            stt_post_speech_ms=stt_speech_end_to_final_ms,
            stt_speech_end_to_final_ms=stt_speech_end_to_final_ms,
            stt_total_ms=stt_speech_end_to_final_ms,
            stt_wall_ms=stt_wall_ms,
            stt_final_ts=now,
            transcript_length=self.transcript_length or None,
            stt_final_transcript_length=self.transcript_length or None,
            stt_final_text_preview=self.stt_final_text_preview or None,
            text_preview=self.stt_final_text_preview or None,
            stt_partial_count=self.stt_partial_count or None,
            deepgram_endpointing_ms=endpointing_ms,
        )
        log_pipeline_event(
            "STT_FINAL_LATENCY",
            room=self.room,
            turn_id=self.turn_id,
            turn_seq=self.turn_seq,
            speech_end_to_final_ms=stt_speech_end_to_final_ms,
            stt_speech_end_to_final_ms=stt_speech_end_to_final_ms,
            stt_total_ms=stt_speech_end_to_final_ms,
            stt_wall_ms=stt_wall_ms,
            deepgram_endpointing_ms=endpointing_ms,
            transcript_length=self.transcript_length or None,
            stt_final_transcript_length=self.transcript_length or None,
            stt_final_text_preview=self.stt_final_text_preview or None,
            stt_partial_count=self.stt_partial_count or None,
            stt_rejected=self.stt_rejected or None,
            low_confidence_rejected=self.low_confidence_rejected or None,
        )

    def mark_transcript_rejected(
        self, *, reason: str, transcript_preview: str = ""
    ) -> None:
        """Record rejected STT (e.g. low confidence) without advancing to LLM."""
        if self.t_stt_final is None and transcript_preview.strip():
            self.mark_stt_final(transcript_preview=transcript_preview)
        elif transcript_preview.strip():
            preview = transcript_preview.strip()
            self.transcript_length = len(preview)
            self.stt_final_text_preview = preview[:80]
        self.stt_rejected = True
        self.low_confidence_rejected = True
        self.stt_rejection_reason = reason
        self._snapshot_stt_phase()
        log_pipeline_event(
            "LOW_CONFIDENCE_TRANSCRIPT_REJECTED",
            room=self.room,
            turn_id=self.turn_id,
            turn_seq=self.turn_seq,
            reason=reason,
            rejection_reason=reason,
            text_preview=(transcript_preview or self.stt_final_text_preview)[:120],
            stt_final_text_preview=self.stt_final_text_preview or None,
            low_confidence_rejected=True,
            stt_rejected=True,
        )
        self.log_stt_latency_summary()

    def build_stt_metrics(self) -> dict[str, Any]:
        """STT-only metrics for summaries; uses snapshot when available."""
        start = self._snap_speech_start
        end = self._snap_speech_end
        first_partial = self._snap_stt_first_partial
        final = self._snap_stt_final
        partial_count = self._snap_partial_count
        preview = self._snap_final_text_preview
        transcript_len = self._snap_transcript_length

        stt_first_partial_ms = self._ms_between(start, first_partial)
        stt_speech_duration_ms = self._ms_between(start, end)
        stt_speech_end_to_final_ms = self._ms_between(end, final)
        if stt_speech_end_to_final_ms is None:
            stt_speech_end_to_final_ms = self._ms_between(
                self._snap_stt_processing_start, final
            )
        stt_wall_ms = self._ms_between(start, final)

        endpointing_ms = (
            _deepgram_endpointing_ms() if self.stt_provider == "deepgram" else None
        )

        metrics: dict[str, Any] = {
            "user_speech_start_ts": start,
            "user_speech_end_ts": end,
            "stt_first_partial_ts": first_partial,
            "stt_final_ts": final,
            "stt_first_partial_ms": stt_first_partial_ms,
            "stt_speech_duration_ms": stt_speech_duration_ms,
            "stt_speech_end_to_final_ms": stt_speech_end_to_final_ms,
            "stt_post_speech_ms": stt_speech_end_to_final_ms,
            # Post-speech latency (USER_SPEECH_END → STT_FINAL), not full turn duration.
            "stt_total_ms": stt_speech_end_to_final_ms,
            "stt_wall_ms": stt_wall_ms,
            "stt_partial_count": partial_count,
            "stt_final_transcript_length": transcript_len,
            "stt_final_text_preview": preview or None,
            "stt_provider": self.stt_provider or None,
            "deepgram_endpointing_ms": endpointing_ms,
            "stt_rejected": self._snap_rejected,
            "low_confidence_rejected": self._snap_low_confidence_rejected,
            "stt_rejection_reason": self._snap_rejection_reason,
            "rejection_reason": self._snap_rejection_reason,
            # Back-compat aliases
            "user_speech_duration_ms": stt_speech_duration_ms,
            "speech_end_to_final_ms": stt_speech_end_to_final_ms,
        }

        metrics["stt_first_partial_ms_missing_reason"] = _missing_reason_ms(
            stt_first_partial_ms,
            start=start,
            end=first_partial,
            start_label="speech_start",
            end_label="first_partial",
        )
        metrics["stt_speech_duration_ms_missing_reason"] = _missing_reason_ms(
            stt_speech_duration_ms,
            start=start,
            end=end,
            start_label="speech_start",
            end_label="speech_end",
        )
        metrics["stt_speech_end_to_final_ms_missing_reason"] = _missing_reason_ms(
            stt_speech_end_to_final_ms,
            start=end if end is not None else self._snap_stt_processing_start,
            end=final,
            start_label="speech_end",
            end_label="stt_final",
        )
        metrics["stt_wall_ms_missing_reason"] = _missing_reason_ms(
            stt_wall_ms,
            start=start,
            end=final,
            start_label="speech_start",
            end_label="stt_final",
        )
        return metrics

    def emit_stt_latency_summary(self) -> None:
        """Emit STT summary immediately after STT final/rejection."""
        if self._snap_stt_final is None and self.t_stt_final is not None:
            self._snapshot_stt_phase()
        self.log_stt_latency_summary()

    def log_stt_latency_summary(self) -> None:
        """Compact STT timing block for one utterance (accepted or rejected)."""
        if not self.turn_id or self._stt_summary_logged:
            return
        self._stt_summary_logged = True
        metrics = self.build_stt_metrics()
        log_pipeline_event(
            "STT_LATENCY_SUMMARY",
            room=self.room,
            turn_id=self.turn_id,
            turn_seq=self.turn_seq,
            **{k: v for k, v in metrics.items() if v is not None},
        )
        stt_log_fields = self._stt_log_block_fields(metrics)
        log_block(
            logger,
            logging.INFO,
            operation="STT_LATENCY",
            step="summary",
            status="REJECTED" if metrics.get("stt_rejected") else "OK",
            room=self.room,
            turn_id=self.turn_id,
            turn_seq=self.turn_seq,
            include_none=True,
            **stt_log_fields,
        )

    def _stt_log_block_fields(self, metrics: dict[str, Any]) -> dict[str, Any]:
        """Flatten STT metrics for PIPELINE_PERF / PIPELINE_LATENCY (nulls explicit)."""
        keys = (
            "user_speech_start_ts",
            "user_speech_end_ts",
            "stt_first_partial_ts",
            "stt_final_ts",
            "stt_first_partial_ms",
            "stt_speech_duration_ms",
            "stt_speech_end_to_final_ms",
            "stt_wall_ms",
            "stt_total_ms",
            "stt_partial_count",
            "stt_final_transcript_length",
            "stt_final_text_preview",
            "stt_provider",
            "deepgram_endpointing_ms",
            "stt_rejected",
            "low_confidence_rejected",
            "stt_rejection_reason",
            "stt_first_partial_ms_missing_reason",
            "stt_speech_duration_ms_missing_reason",
            "stt_speech_end_to_final_ms_missing_reason",
            "stt_wall_ms_missing_reason",
        )
        return {k: metrics.get(k) for k in keys}

    def mark_llm_dispatch(self) -> None:
        now = time.perf_counter()
        if self.t_llm_dispatch is None:
            self.t_llm_dispatch = now
            self._log_perf(
                "LLM_REQUEST_START",
                segment="llm",
                segment_ms=self._ms_between(self._snap_stt_final, now),
            )

    def mark_llm_first_token(self, *, token_preview: str = "") -> None:
        now = time.perf_counter()
        if self.t_llm_first_token is None:
            self.t_llm_first_token = now
            ttft = self._ms_between(self.t_llm_dispatch, now)
            self._log_perf(
                "LLM_FIRST_TOKEN",
                segment="llm",
                segment_ms=ttft,
                time_to_first_token_ms=ttft,
                e2e_after_stt_final_ms=self._ms_between(self._snap_stt_final, now),
                token_preview=token_preview[:40] if token_preview else None,
            )

    def mark_llm_complete(self, *, response_length: int | None = None) -> None:
        now = time.perf_counter()
        first = self.t_llm_complete is None
        if first:
            self.t_llm_complete = now
        if response_length is not None:
            self.assistant_response_length = max(
                self.assistant_response_length, response_length
            )
        if not first:
            return
        self._log_perf(
            "LLM_RESPONSE_COMPLETE",
            segment="llm",
            segment_ms=self._ms_between(self.t_llm_dispatch, now),
            llm_total_ms=self._ms_between(self.t_llm_dispatch, now),
            response_length=self.assistant_response_length or None,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
        )

    def record_llm_usage(
        self,
        *,
        prompt_tokens: int | None = None,
        completion_tokens: int | None = None,
        total_tokens: int | None = None,
    ) -> None:
        if prompt_tokens is not None:
            self.prompt_tokens = prompt_tokens
        if completion_tokens is not None:
            self.completion_tokens = completion_tokens
        if total_tokens is not None:
            self.total_tokens = total_tokens

    def mark_tts_llm_input_first(self, *, token_preview: str = "") -> None:
        if self.t_tts_llm_input_first is None:
            self.t_tts_llm_input_first = time.perf_counter()

    def mark_tts_http_start(self, *, text_chars: int, mode: str) -> None:
        now = time.perf_counter()
        if self.t_tts_request_start is None:
            self.t_tts_request_start = now
        log_pipeline_event(
            "TTS_REQUEST_START",
            room=self.room,
            turn_id=self.turn_id,
            text_chars=text_chars,
            tts_mode=mode,
            tts_provider=self.tts_provider or None,
            tts_voice=self.tts_voice or None,
            since_llm_phase_ms=self._since_llm_phase_ms(now),
        )

    def mark_tts_http_complete(self, *, timing_ms: int, bytes_received: int) -> None:
        now = time.perf_counter()
        self.t_tts_http_complete = now
        self.tts_http_total_ms += timing_ms
        self.tts_bytes_received += bytes_received
        ttfb = self._ms_between(self.t_tts_request_start, self.t_tts_first_server_chunk)
        log_pipeline_event(
            "TTS_DONE",
            room=self.room,
            turn_id=self.turn_id,
            timing_ms=timing_ms,
            bytes_received=bytes_received,
            tts_request_ms=timing_ms,
            tts_http_total_ms=self.tts_http_total_ms or None,
            time_to_first_audio_ms=ttfb,
            tts_provider=self.tts_provider or None,
            tts_voice=self.tts_voice or None,
            since_llm_phase_ms=self._since_llm_phase_ms(now),
        )

    def mark_tts_first_server_chunk(self) -> None:
        now = time.perf_counter()
        if self.t_tts_first_server_chunk is None:
            self.t_tts_first_server_chunk = now
            log_pipeline_event(
                "TTS_FIRST_AUDIO_CHUNK",
                room=self.room,
                turn_id=self.turn_id,
                time_to_first_audio_ms=self._ms_between(
                    self.t_tts_request_start, now
                ),
                e2e_after_stt_final_ms=self._ms_between(self._snap_stt_final, now),
                tts_provider=self.tts_provider or None,
                tts_voice=self.tts_voice or None,
                since_llm_phase_ms=self._since_llm_phase_ms(now),
            )

    def mark_tts_first_playout_chunk(self, *, chunk_bytes: int) -> None:
        now = time.perf_counter()
        if self.t_playback_start is None:
            self.mark_playback_start()
        if self.t_playback_first_chunk is None:
            self.t_playback_first_chunk = now
            e2e = self._ms_between(self._snap_stt_final, now)
            log_pipeline_event(
                "AUDIO_PLAYOUT_START",
                room=self.room,
                turn_id=self.turn_id,
                chunk_bytes=chunk_bytes,
                e2e_after_stt_final_ms=e2e,
                since_llm_phase_ms=self._since_llm_phase_ms(now),
            )
            log_pipeline_event(
                "AUDIO_PLAYOUT_FIRST_CHUNK",
                room=self.room,
                turn_id=self.turn_id,
                chunk_bytes=chunk_bytes,
                e2e_after_stt_final_ms=e2e,
            )

    def mark_playback_start(self) -> None:
        now = time.perf_counter()
        if self.t_playback_start is None:
            self.t_playback_start = now
            self._log_perf(
                "PLAYBACK_SEND_START",
                segment="playback",
                segment_ms=self._ms_between(self.t_tts_http_complete, now),
            )

    def mark_playback_complete(self) -> None:
        now = time.perf_counter()
        if self.t_playback_complete is None:
            self.t_playback_complete = now
            push_ms = self._ms_between(self.t_playback_start, now)
            self._log_perf(
                "PLAYBACK_SEND_COMPLETE",
                segment="playback",
                segment_ms=push_ms,
                audio_push_duration_ms=push_ms,
            )
            if self.t_llm_dispatch is not None and not self._logged:
                self.log_turn_summary()

    def build_summary(self) -> dict[str, Any]:
        """Aggregate timings — prefer metrics that match perceived latency."""
        stt = self.build_stt_metrics()

        e2e_to_first_audio_ms = self._ms_between(
            self._snap_stt_final, self.t_playback_first_chunk
        )
        if e2e_to_first_audio_ms is None:
            e2e_to_first_audio_ms = self._ms_between(
                self._snap_stt_final, self.t_tts_first_server_chunk
            )

        playback_total_ms = self._ms_between(
            self.t_playback_start, self.t_playback_complete
        )
        e2e_to_playback_complete_ms = self._ms_between(
            self._snap_stt_final, self.t_playback_complete
        )

        summary: dict[str, Any] = {
            "turn_id": self.turn_id,
            "turn_seq": self.turn_seq or None,
            **stt,
            "llm_total_ms": self._ms_between(self.t_llm_dispatch, self.t_llm_complete),
            "llm_time_to_first_token_ms": self._ms_between(
                self.t_llm_dispatch, self.t_llm_first_token
            ),
            "llm_start_after_stt_final_ms": self._ms_between(
                self._snap_stt_final, self.t_llm_dispatch
            ),
            "tts_http_total_ms": self.tts_http_total_ms or None,
            "tts_time_to_first_audio_ms": self._ms_between(
                self.t_tts_request_start, self.t_tts_first_server_chunk
            ),
            "tts_input_to_first_chunk_ms": self._ms_between(
                self.t_tts_llm_input_first, self.t_tts_first_server_chunk
            ),
            "e2e_to_first_audio_ms": e2e_to_first_audio_ms,
            "e2e_to_playback_complete_ms": e2e_to_playback_complete_ms,
            "audio_push_duration_ms": playback_total_ms,
            "playback_total_ms": playback_total_ms,
            "transcript_length": stt.get("stt_final_transcript_length"),
            "assistant_response_length": self.assistant_response_length or None,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
            "tts_provider": self.tts_provider or None,
            "tts_voice": self.tts_voice or None,
            "llm_model": self.llm_model or None,
        }
        return summary

    def _log_pipeline_turn_block(self, *, operation: str, step: str) -> None:
        summary = self.build_summary()
        stt_fields = self._stt_log_block_fields(summary)
        other_fields = {
            k: v
            for k, v in summary.items()
            if k not in stt_fields
            and k not in ("turn_id", "turn_seq")
            and v is not None
        }
        log_block(
            logger,
            logging.INFO,
            operation=operation,
            step=step,
            status="REJECTED" if summary.get("stt_rejected") else "OK",
            room=self.room,
            turn_id=self.turn_id,
            turn_seq=summary.get("turn_seq"),
            include_none=True,
            **stt_fields,
            **other_fields,
        )

    def log_turn_once(self) -> None:
        if self._logged:
            return
        self._logged = True
        self._log_pipeline_turn_block(operation="PIPELINE_LATENCY", step="turn")

    def log_turn_summary(self) -> None:
        """Full turn performance summary (STT + LLM + TTS + playback)."""
        if self._logged:
            return
        self.log_stt_latency_summary()
        summary = self.build_summary()
        self._log_pipeline_turn_block(
            operation="PIPELINE_PERF", step="turn_complete"
        )
        event_fields = {
            k: v for k, v in summary.items() if v is not None and k != "turn_id"
        }
        log_pipeline_event(
            "TURN_COMPLETE",
            room=self.room,
            turn_id=self.turn_id,
            **event_fields,
        )
        self.log_turn_once()
