"""Finalize stable partial STT text when the user stops speaking (WLK may not send ready_to_stop)."""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable

from agent.observability.pipeline_events import log_pipeline_event

logger = logging.getLogger("agent.pipeline.stt_partial_finalize")


def partial_finalize_timeout_ms() -> int:
    try:
        ms = int(os.environ.get("VOICE_AGENT_STT_PARTIAL_FINALIZE_MS", "750"))
    except ValueError:
        ms = 750
    return max(600, min(900, ms))


class PartialFinalizeController:
    """
    When partial text stops changing for ``timeout_ms``, invoke ``emit_final`` once.

    Does not alter STT audio/model settings — only turn-finalization timing.
    """

    def __init__(
        self,
        *,
        emit_final: Callable[[str], bool],
        get_text: Callable[[], str],
        is_already_final: Callable[[], bool],
        min_chars: int = 3,
        timeout_ms: int | None = None,
    ) -> None:
        self._emit_final = emit_final
        self._get_text = get_text
        self._is_already_final = is_already_final
        self._min_chars = max(1, min_chars)
        self._timeout_ms = timeout_ms if timeout_ms is not None else partial_finalize_timeout_ms()
        self._task: asyncio.Task[None] | None = None
        self._armed_text = ""

    def cancel(self, *, reason: str) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            log_pipeline_event(
                "STT_FINALIZATION_TIMER_CANCELLED",
                reason=reason,
                text_preview=self._armed_text[:80],
            )
        self._task = None
        self._armed_text = ""

    def on_partial(self, text: str) -> None:
        """Restart the finalize timer after a new partial transcript."""
        if self._is_already_final():
            return
        cleaned = (text or "").strip()
        if len(cleaned) < self._min_chars:
            return

        if self._task is not None and not self._task.done() and cleaned == self._armed_text:
            return

        self.cancel(reason="new_partial")
        self._armed_text = cleaned
        log_pipeline_event(
            "STT_FINALIZATION_TIMER_STARTED",
            timeout_ms=self._timeout_ms,
            text_preview=cleaned[:80],
        )
        self._task = asyncio.create_task(self._run_timer())

    async def _run_timer(self) -> None:
        try:
            await asyncio.sleep(self._timeout_ms / 1000.0)
        except asyncio.CancelledError:
            return

        if self._is_already_final():
            return

        text = (self._get_text() or self._armed_text).strip()
        if len(text) < self._min_chars:
            return

        log_pipeline_event(
            "STT_FINALIZATION_TIMEOUT",
            timeout_ms=self._timeout_ms,
            text_preview=text[:80],
        )
        self._emit_final(text)
        self._task = None

    async def aclose(self) -> None:
        self.cancel(reason="stream_close")
