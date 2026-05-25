"""Prefetch sentence TTS and play with minimal gap between phrases."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable

from agent.observability.latency import TtsCallTiming
from agent.pipeline.tts_audio_utils import pcm_to_emit_chunks, prepare_phrase_pcm
from livekit.agents import tts


def _trim_enabled() -> bool:
    return os.environ.get("VOICE_AGENT_TTS_TRIM_PHRASE_SILENCE", "true").lower() != "false"


def _prefetch_depth() -> int:
    try:
        return max(1, int(os.environ.get("VOICE_AGENT_TTS_PHRASE_PREFETCH", "2")))
    except ValueError:
        return 2


async def run_prefetched_phrase_playback(
    phrase_iter: AsyncIterator[str],
    *,
    sample_rate: int,
    emit_chunk_bytes: int,
    fetch_phrase_audio: FetchPhraseAudio,
    play_chunks: Callable[..., Awaitable[None]],
    output_emitter: tts.AudioEmitter,
    on_timing: Callable[[TtsCallTiming], None] | None,
) -> None:
    """
    Fetch upcoming sentences while the current one plays out.

    A bounded queue lets synthesis run 1–2 phrases ahead of playback so the next
    sentence can start as soon as the previous finishes.
    """
    queue: asyncio.Queue[
        tuple[list[bytes], TtsCallTiming | None] | None
    ] = asyncio.Queue(maxsize=_prefetch_depth())
    trim = _trim_enabled()

    async def fetcher() -> None:
        strip_wav = False
        try:
            async for phrase in phrase_iter:
                raw, timing = await fetch_phrase_audio(phrase, strip_wav_header=strip_wav)
                pcm = prepare_phrase_pcm(
                    raw,
                    sample_rate=sample_rate,
                    strip_wav_header=strip_wav,
                    trim_leading=trim and strip_wav,
                    trim_trailing=trim,
                )
                chunks = pcm_to_emit_chunks(pcm, emit_chunk_bytes)
                await queue.put((chunks, timing))
                strip_wav = True
        finally:
            await queue.put(None)

    first_play = True
    try:
        fetch_task = asyncio.create_task(fetcher())
        while True:
            item = await queue.get()
            if item is None:
                break
            chunks, timing = item
            await play_chunks(
                chunks,
                output_emitter,
                mark_started=first_play,
            )
            first_play = False
            if timing and on_timing:
                on_timing(timing)
    finally:
        if not fetch_task.done():
            fetch_task.cancel()
            try:
                await fetch_task
            except asyncio.CancelledError:
                pass
