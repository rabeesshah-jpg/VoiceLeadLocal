"""Timing instrumentation for the official livekit-plugins-cartesia TTS.

Cartesia's own TTS/ChunkedStream/SynthesizeStream classes provide no hooks
for per-call timing (unlike the hand-written multilingual/supertonic TTS
implementations in this codebase, which have on_timing/pipeline_tracker
built in from the start). Reimplementing Cartesia's actual API client to
match that same pattern would mean rewriting a currently-working,
production TTS integration from scratch — a large, unnecessary risk.

Instead, this subclasses Cartesia's real classes and calls their real,
*unmodified* `_run()` implementation exactly as-is — the actual API calls,
WebSocket streaming, tokenizer, and pacing logic are completely untouched.
Timing is captured purely by wrapping the `output_emitter` object that
`_run()` writes audio into: the wrapper notices the first chunk pushed
through it (time-to-first-byte) and the moment `_run()` returns (total
call time), then reports both via the same TtsCallTiming/pipeline_tracker
interface the other providers already use.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS
from livekit.plugins.cartesia.tts import (
    TTS as _CartesiaTTS,
)
from livekit.plugins.cartesia.tts import (
    ChunkedStream as _CartesiaChunkedStream,
)
from livekit.plugins.cartesia.tts import (
    SynthesizeStream as _CartesiaSynthesizeStream,
)

from agent.observability.latency import TtsCallTiming
from agent.observability.pipeline_latency import TurnPipelineTracker


class _TimingEmitterProxy:
    """Transparent pass-through for the real AudioEmitter — marks only the
    first push() call, forwards everything else (initialize, start_segment,
    flush, end_segment, and any other attribute) straight to the real
    emitter, unmodified.
    """

    def __init__(self, emitter, on_first_push: Callable[[int], None]) -> None:
        self._emitter = emitter
        self._on_first_push = on_first_push
        self._first = True

    def push(self, chunk: bytes):
        if self._first:
            self._first = False
            self._on_first_push(len(chunk))
        return self._emitter.push(chunk)

    def __getattr__(self, name):
        return getattr(self._emitter, name)


def _report_timing(
    *,
    t0: float,
    first_chunk_ts: float | None,
    text_chars: int,
    text_preview: str,
    pipeline_tracker: TurnPipelineTracker | None,
    on_timing: Callable[[TtsCallTiming], None] | None,
) -> None:
    total_ms = int((time.perf_counter() - t0) * 1000)
    ttfb_ms = int((first_chunk_ts - t0) * 1000) if first_chunk_ts else total_ms
    if pipeline_tracker:
        pipeline_tracker.mark_tts_http_complete(timing_ms=total_ms, bytes_received=0)
    if on_timing:
        on_timing(
            TtsCallTiming(
                text_preview=text_preview[:80],
                chars=text_chars,
                ttfb_ms=max(0, ttfb_ms),
                total_ms=total_ms,
                bytes_received=0,
            )
        )


class _InstrumentedChunkedStream(_CartesiaChunkedStream):
    def __init__(
        self,
        *,
        tts,
        input_text: str,
        conn_options,
        pipeline_tracker: TurnPipelineTracker | None = None,
        on_timing: Callable[[TtsCallTiming], None] | None = None,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._pipeline_tracker = pipeline_tracker
        self._on_timing = on_timing

    async def _run(self, output_emitter) -> None:
        t0 = time.perf_counter()
        first_chunk_ts: list[float | None] = [None]

        def _on_first_push(_nbytes: int) -> None:
            first_chunk_ts[0] = time.perf_counter()
            if self._pipeline_tracker:
                self._pipeline_tracker.mark_tts_first_server_chunk()

        if self._pipeline_tracker:
            self._pipeline_tracker.mark_tts_http_start(
                text_chars=len(self._input_text), mode="cartesia_chunked"
            )
        proxy = _TimingEmitterProxy(output_emitter, _on_first_push)
        await super()._run(proxy)  # real Cartesia HTTP call — unmodified
        _report_timing(
            t0=t0,
            first_chunk_ts=first_chunk_ts[0],
            text_chars=len(self._input_text),
            text_preview=self._input_text,
            pipeline_tracker=self._pipeline_tracker,
            on_timing=self._on_timing,
        )


class _InstrumentedSynthesizeStream(_CartesiaSynthesizeStream):
    def __init__(
        self,
        *,
        tts,
        conn_options,
        pipeline_tracker: TurnPipelineTracker | None = None,
        on_timing: Callable[[TtsCallTiming], None] | None = None,
        on_llm_first_token: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._pipeline_tracker = pipeline_tracker
        self._on_timing = on_timing
        self._on_llm_first_token = on_llm_first_token

    async def _run(self, output_emitter) -> None:
        t0 = time.perf_counter()
        first_chunk_ts: list[float | None] = [None]

        def _on_first_push(_nbytes: int) -> None:
            first_chunk_ts[0] = time.perf_counter()
            if self._pipeline_tracker:
                self._pipeline_tracker.mark_tts_first_server_chunk()

        if self._pipeline_tracker:
            self._pipeline_tracker.mark_tts_http_start(text_chars=0, mode="cartesia_stream")
        proxy = _TimingEmitterProxy(output_emitter, _on_first_push)
        await super()._run(proxy)  # real Cartesia WebSocket streaming — unmodified
        _report_timing(
            t0=t0,
            first_chunk_ts=first_chunk_ts[0],
            text_chars=0,
            text_preview="",
            pipeline_tracker=self._pipeline_tracker,
            on_timing=self._on_timing,
        )


class InstrumentedCartesiaTTS(_CartesiaTTS):
    """Drop-in replacement for cartesia.TTS — identical behavior, adds
    real timing marks (mark_tts_http_start/mark_tts_first_server_chunk/
    mark_tts_http_complete + on_timing) around the unmodified base class
    synthesis calls.
    """

    def __init__(
        self,
        *,
        pipeline_tracker: TurnPipelineTracker | None = None,
        on_timing: Callable[[TtsCallTiming], None] | None = None,
        on_llm_first_token: Callable[[], None] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._instr_pipeline_tracker = pipeline_tracker
        self._instr_on_timing = on_timing
        self._instr_on_llm_first_token = on_llm_first_token

    def synthesize(
        self, text: str, *, conn_options=DEFAULT_API_CONNECT_OPTIONS
    ) -> _InstrumentedChunkedStream:
        return _InstrumentedChunkedStream(
            tts=self,
            input_text=text,
            conn_options=conn_options,
            pipeline_tracker=self._instr_pipeline_tracker,
            on_timing=self._instr_on_timing,
        )

    def stream(
        self, *, conn_options=DEFAULT_API_CONNECT_OPTIONS
    ) -> _InstrumentedSynthesizeStream:
        stream = _InstrumentedSynthesizeStream(
            tts=self,
            conn_options=conn_options,
            pipeline_tracker=self._instr_pipeline_tracker,
            on_timing=self._instr_on_timing,
            on_llm_first_token=self._instr_on_llm_first_token,
        )
        # Preserve the base class's cleanup bookkeeping (TTS.aclose()
        # iterates self._streams) — same behavior as the real stream().
        self._streams.add(stream)
        return stream