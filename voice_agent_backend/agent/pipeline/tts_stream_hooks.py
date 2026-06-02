"""Hooks on the LLM→TTS token stream (first-token latency, pipeline markers)."""

from __future__ import annotations

from collections.abc import Callable

from agent.observability.pipeline_latency import TurnPipelineTracker


def note_first_llm_token(
    *,
    already_noted: bool,
    token: str,
    pipeline: TurnPipelineTracker | None,
    on_llm_first_token: Callable[[], None] | None,
) -> bool:
    """Record the first non-empty LLM token sent to TTS. Returns updated noted flag."""
    if already_noted or not str(token).strip():
        return already_noted
    if pipeline is not None and pipeline.t_tts_llm_input_first is None:
        pipeline.mark_tts_llm_input_first(token_preview=str(token)[:40])
        pipeline.mark_llm_first_token(token_preview=str(token))
    if on_llm_first_token is not None:
        on_llm_first_token()
    return True
