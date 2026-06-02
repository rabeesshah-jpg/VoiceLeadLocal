"""Guards for one stable STT final → one LLM request per user turn."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field

from agent.observability.streaming_audit import log_streaming_audit
from agent.pipeline.stt_transcript_utils import (
    clean_transcript,
    extract_stt_delta,
    is_duplicate_final_for_send,
)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def final_stability_ms() -> int:
    from agent.pipeline.stt_config import is_deepgram_provider

    default = 0 if is_deepgram_provider() else 200
    return max(0, _env_int("VOICE_AGENT_STT_FINAL_STABILITY_MS", default))


def barge_in_enabled() -> bool:
    return _env_bool("VOICE_AGENT_BARGE_IN_ENABLED", False)


@dataclass
class TurnCommitController:
    """Tracks committed finals and blocks duplicate LLM dispatches."""

    committed_final_ref: list[str] = field(default_factory=lambda: [""])
    last_sent_final_ref: list[str] = field(default_factory=lambda: [""])
    llm_started_turn_seqs: set[int] = field(default_factory=set)
    llm_dispatched_pipeline_turn_ids: set[str] = field(default_factory=set)
    _commit_generation: int = 0
    _pending_commit_task: asyncio.Task[None] | None = None

    @property
    def last_committed_final(self) -> str:
        return self.committed_final_ref[0] if self.committed_final_ref else ""

    @property
    def last_sent_final(self) -> str:
        return self.last_sent_final_ref[0] if self.last_sent_final_ref else ""

    def delta_from_incoming(self, incoming: str) -> str:
        return extract_stt_delta(self.last_committed_final, incoming)

    def should_send_final(self, delta: str) -> tuple[bool, str]:
        """Return (ok, reject_reason)."""
        is_dup, reason = is_duplicate_final_for_send(
            delta=delta,
            last_sent_final=self.last_sent_final,
            last_committed_cumulative=self.last_committed_final,
        )
        if is_dup:
            return False, reason or "duplicate"
        return True, ""

    def record_sent_final(self, delta: str) -> None:
        if self.last_sent_final_ref is not None:
            self.last_sent_final_ref[0] = clean_transcript(delta)

    def cancel_pending_commit(self, *, reason: str, turn_id: str = "", room: str = "") -> None:
        self._commit_generation += 1
        task = self._pending_commit_task
        self._pending_commit_task = None
        if task and not task.done():
            task.cancel()
        if reason:
            log_streaming_audit(
                "stt_final_superseded_pending_llm",
                turn_id=turn_id,
                room=room,
                reason=reason,
            )

    def schedule_final_commit(
        self,
        *,
        turn_id: str,
        room: str,
        turn_seq: int,
        full_text: str,
        commit_fn,
    ) -> None:
        """Debounce final commits so a superseding STT final can replace the pending one."""
        self.cancel_pending_commit(reason="", turn_id=turn_id, room=room)
        self._commit_generation += 1
        generation = self._commit_generation

        async def _run() -> None:
            delay_s = final_stability_ms() / 1000.0
            if delay_s > 0:
                await asyncio.sleep(delay_s)
            if generation != self._commit_generation:
                return
            await commit_fn(full_text=full_text, turn_seq=turn_seq, generation=generation)

        self._pending_commit_task = asyncio.create_task(_run())

    def record_committed_final(self, full_text: str) -> None:
        from agent.pipeline.stt_transcript_utils import clean_transcript

        if self.committed_final_ref is not None:
            self.committed_final_ref[0] = clean_transcript(full_text)

    def allow_llm_request(
        self,
        *,
        turn_seq: int,
        pipeline_turn_id: str,
        room: str = "",
    ) -> bool:
        if pipeline_turn_id in self.llm_dispatched_pipeline_turn_ids:
            log_streaming_audit(
                "duplicate_llm_request_blocked",
                turn_id=pipeline_turn_id,
                room=room,
                turn_seq=turn_seq,
                reason="pipeline_turn_id_already_dispatched",
            )
            return False
        if turn_seq in self.llm_started_turn_seqs:
            log_streaming_audit(
                "duplicate_llm_request_blocked",
                turn_id=pipeline_turn_id,
                room=room,
                turn_seq=turn_seq,
                reason="turn_seq_already_started",
            )
            return False
        log_streaming_audit(
            "llm_request_allowed",
            turn_id=pipeline_turn_id,
            room=room,
            turn_seq=turn_seq,
        )
        return True

    def mark_llm_started(self, *, turn_seq: int, pipeline_turn_id: str) -> None:
        self.llm_started_turn_seqs.add(turn_seq)
        self.llm_dispatched_pipeline_turn_ids.add(pipeline_turn_id)

    def clear_turn_seq(self, turn_seq: int) -> None:
        self.llm_started_turn_seqs.discard(turn_seq)
