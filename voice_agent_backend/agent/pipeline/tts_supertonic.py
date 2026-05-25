"""Supertonic-3 local HTTP TTS — POST /v1/tts returns a complete WAV (44.1 kHz)."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass

import aiohttp

from agent.observability.latency import TtsCallTiming
from agent.observability.pipeline_latency import TurnPipelineTracker
from agent.pipeline.tts_audio_utils import prepare_phrase_pcm, strip_leading_wav_header
from agent.pipeline.tts_http_pool import ensure_tts_http_session
from agent.pipeline.tts_phrase_playback import run_prefetched_phrase_playback
from agent.observability.pipeline_events import log_pipeline_event, tts_stream_mode_label
from agent.pipeline.tts_stream_hooks import note_first_llm_token
from agent.pipeline.tts_text_chunker import SentenceTextChunker

from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    tts,
    utils,
)
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS

# Supertonic serve outputs 44.1 kHz mono WAV (see GET /v1/health sample_rate).
SAMPLE_RATE = 44100
NUM_CHANNELS = 1
WAV_HEADER_BYTES = 44
DEFAULT_BASE_URL = "http://127.0.0.1:7788"
DEFAULT_VOICE = "M1"
DEFAULT_LANG = "en"
DEFAULT_MODEL = "supertonic-3"
EMIT_CHUNK_BYTES = 4096

logger = logging.getLogger("agent.pipeline.tts_supertonic")

# Back-compat alias used by streaming phrase mode.
StreamingTextChunker = SentenceTextChunker


def _normalize_base_url(url: str) -> str:
    u = url.strip().rstrip("/")
    for suffix in ("/v1/tts", "/tts", "/v1/audio/speech"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
    return u.rstrip("/")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _tts_env(name: str, legacy: str, default: str) -> str:
    return (os.environ.get(name) or os.environ.get(legacy) or default).strip()


@dataclass
class _TTSOptions:
    base_url: str
    voice: str
    lang: str
    model: str
    response_format: str
    max_chunk_length: int | None
    min_text_chars: int
    max_text_chars: int


def _build_payload(opts: _TTSOptions, text: str) -> dict:
    payload: dict = {
        "text": text,
        "voice": opts.voice,
        "lang": opts.lang,
        "response_format": opts.response_format,
    }
    if opts.max_chunk_length:
        payload["max_chunk_length"] = opts.max_chunk_length
    return payload


class TTS(tts.TTS):
    def __init__(
        self,
        *,
        base_url: str | None = None,
        voice: str | None = None,
        lang: str | None = None,
        model: str | None = None,
        response_format: str = "wav",
        max_chunk_length: int | None = None,
        min_text_chars: int = 20,
        max_text_chars: int = 80,
        http_session: aiohttp.ClientSession | None = None,
        on_timing: Callable[[TtsCallTiming], None] | None = None,
        pipeline_tracker: TurnPipelineTracker | None = None,
        on_llm_first_token: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
        )
        self._opts = _TTSOptions(
            base_url=_normalize_base_url(
                base_url or _tts_env("TTS_BASE_URL", "CHATTERBOX_TTS_URL", DEFAULT_BASE_URL)
            ),
            voice=voice or _tts_env("TTS_VOICE", "VOICE_AGENT_CHATTERBOX_VOICE_ID", DEFAULT_VOICE),
            lang=lang or _tts_env("TTS_LANG", "", DEFAULT_LANG),
            model=model or _tts_env("TTS_MODEL", "", DEFAULT_MODEL),
            response_format=response_format,
            max_chunk_length=max_chunk_length,
            min_text_chars=min_text_chars,
            max_text_chars=max_text_chars,
        )
        self._session = http_session
        self._on_timing = on_timing
        self._pipeline = pipeline_tracker
        self._on_llm_first_token = on_llm_first_token

    @property
    def model(self) -> str:
        return self._opts.model

    @property
    def provider(self) -> str:
        return "Supertonic"

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is not None and not self._session.closed:
            return self._session
        self._session = await ensure_tts_http_session()
        return self._session

    def synthesize(
        self, text: str, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> ChunkedStream:
        return ChunkedStream(tts=self, input_text=text, conn_options=conn_options)

    def stream(
        self, *, conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS
    ) -> SynthesizeStream:
        return SynthesizeStream(tts=self, conn_options=conn_options)

    async def aclose(self) -> None:
        self._session = None


class ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts: TTS, input_text: str, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts = tts

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            mime_type="audio/wav",
            stream=False,
        )
        session = await self._tts._ensure_session()
        if self._tts._pipeline:
            self._tts._pipeline.mark_tts_http_start(
                text_chars=len(self._input_text),
                mode="single_post_full_wav",
            )
        chunks, timing = await _fetch_audio_chunks(
            session=session,
            opts=self._tts._opts,
            text=self._input_text,
            conn_options=self._conn_options,
            skip_wav_header=False,
            pipeline=self._tts._pipeline,
        )
        first = True
        for chunk in chunks:
            if first:
                self._mark_started()
                if self._tts._pipeline:
                    self._tts._pipeline.mark_tts_first_playout_chunk(chunk_bytes=len(chunk))
                first = False
            elif self._tts._pipeline:
                self._tts._pipeline.mark_tts_first_playout_chunk(chunk_bytes=len(chunk))
            output_emitter.push(chunk)
        output_emitter.flush()


def _stream_phrases_enabled() -> bool:
    return os.environ.get("VOICE_AGENT_TTS_STREAM_PHRASES", "false").lower() == "true"


class SynthesizeStream(tts.SynthesizeStream):
    """One /v1/tts call per reply (default) or phrase mode with prefetch."""

    def __init__(self, *, tts: TTS, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts = tts
        self._first_llm_token_noted = False

    def _note_first_llm_token(self, token: str) -> None:
        self._first_llm_token_noted = note_first_llm_token(
            already_noted=self._first_llm_token_noted,
            token=token,
            pipeline=self._tts._pipeline,
            on_llm_first_token=self._tts._on_llm_first_token,
        )

    async def _iter_phrases(self):
        chunker = StreamingTextChunker(
            min_chars=self._tts._opts.min_text_chars,
            max_chars=self._tts._opts.max_text_chars,
        )
        async for data in self._input_ch:
            if isinstance(data, self._FlushSentinel):
                for phrase in chunker.flush():
                    if phrase.strip():
                        yield phrase
                continue
            token = str(data)
            self._note_first_llm_token(token)
            for phrase in chunker.push(token):
                if phrase.strip():
                    yield phrase
        for phrase in chunker.flush():
            if phrase.strip():
                yield phrase
        pipeline = self._tts._pipeline
        if pipeline:
            log_pipeline_event(
                "LLM_DONE",
                room=pipeline.room,
                turn_id=pipeline.turn_id,
                tts_mode=tts_stream_mode_label(),
                note="llm_to_tts_input_channel_closed",
            )

    async def _play_chunks(
        self, chunks: list[bytes], output_emitter: tts.AudioEmitter, *, mark_started: bool
    ) -> None:
        pipeline = self._tts._pipeline
        for i, chunk in enumerate(chunks):
            if not chunk:
                continue
            if mark_started and i == 0:
                self._mark_started()
            if pipeline:
                pipeline.mark_tts_first_playout_chunk(chunk_bytes=len(chunk))
            output_emitter.push(chunk)

    async def _run_phrase_stream(self, output_emitter: tts.AudioEmitter) -> None:
        session = await self._tts._ensure_session()
        tts_ref = self._tts

        async def fetch_phrase(phrase: str, *, strip_wav_header: bool) -> tuple[bytes, TtsCallTiming | None]:
            if tts_ref._pipeline:
                tts_ref._pipeline.mark_tts_http_start(
                    text_chars=len(phrase),
                    mode="phrase_post_per_sentence",
                )
            return await _fetch_phrase_body(
                session=session,
                opts=tts_ref._opts,
                text=phrase,
                conn_options=self._conn_options,
                pipeline=tts_ref._pipeline,
            )

        await run_prefetched_phrase_playback(
            self._iter_phrases(),
            sample_rate=SAMPLE_RATE,
            emit_chunk_bytes=EMIT_CHUNK_BYTES,
            fetch_phrase_audio=fetch_phrase,
            play_chunks=self._play_chunks,
            output_emitter=output_emitter,
            on_timing=self._tts._on_timing,
        )

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=SAMPLE_RATE,
            num_channels=NUM_CHANNELS,
            mime_type="audio/wav",
            stream=True,
        )
        segment_id = utils.shortuuid()
        output_emitter.start_segment(segment_id=segment_id)
        try:
            if _stream_phrases_enabled():
                await self._run_phrase_stream(output_emitter)
                return

            parts: list[str] = []
            async for data in self._input_ch:
                if isinstance(data, self._FlushSentinel):
                    continue
                token = str(data)
                self._note_first_llm_token(token)
                parts.append(token)
            pipeline = self._tts._pipeline
            if pipeline:
                log_pipeline_event(
                    "LLM_DONE",
                    room=pipeline.room,
                    turn_id=pipeline.turn_id,
                    tts_mode="single_post_after_llm_buffered",
                    note="llm_to_tts_input_channel_closed",
                )
            full_text = "".join(parts).strip()
            if not full_text:
                return
            session = await self._tts._ensure_session()
            if self._tts._pipeline:
                self._tts._pipeline.mark_tts_http_start(
                    text_chars=len(full_text),
                    mode="single_post_after_llm_buffered",
                )
            chunks, timing = await _fetch_audio_chunks(
                session=session,
                opts=self._tts._opts,
                text=full_text,
                conn_options=self._conn_options,
                skip_wav_header=False,
                pipeline=self._tts._pipeline,
            )
            await self._play_chunks(chunks, output_emitter, mark_started=True)
            if timing and self._tts._on_timing:
                self._tts._on_timing(timing)
        finally:
            output_emitter.end_segment()


async def _fetch_phrase_body(
    *,
    session: aiohttp.ClientSession,
    opts: _TTSOptions,
    text: str,
    conn_options: APIConnectOptions,
    pipeline: TurnPipelineTracker | None = None,
) -> tuple[bytes, TtsCallTiming | None]:
    """Download full WAV body for one phrase (trim/slice applied by phrase playback)."""
    text = text.strip()
    if not text:
        return b"", None

    endpoint = f"{opts.base_url}/v1/tts"
    payload = _build_payload(opts, text)
    t0 = time.perf_counter()

    try:
        async with session.post(
            endpoint,
            json=payload,
            timeout=aiohttp.ClientTimeout(
                total=300,
                sock_connect=conn_options.timeout,
            ),
        ) as resp:
            if resp.status != 200:
                body = (await resp.text())[:300]
                raise APIStatusError(
                    message=f"Supertonic TTS HTTP {resp.status}: {body}",
                    status_code=resp.status,
                    request_id=None,
                    body=body,
                )
            data_parts: list[bytes] = []
            async for chunk in resp.content.iter_any():
                if not chunk:
                    continue
                if pipeline:
                    pipeline.mark_tts_first_server_chunk()
                data_parts.append(chunk)
            data = b"".join(data_parts)
    except asyncio.TimeoutError:
        raise APITimeoutError() from None
    except aiohttp.ClientError as e:
        raise APIConnectionError() from e

    total_ms = int((time.perf_counter() - t0) * 1000)
    ttfb_ms = (
        int((pipeline.t_tts_first_server_chunk - t0) * 1000)
        if pipeline and pipeline.t_tts_first_server_chunk
        else total_ms
    )
    timing = TtsCallTiming(
        text_preview=text[:80],
        chars=len(text),
        ttfb_ms=max(0, ttfb_ms),
        total_ms=total_ms,
        bytes_received=len(data),
    )
    if pipeline:
        pipeline.mark_tts_http_complete(
            timing_ms=timing.total_ms, bytes_received=timing.bytes_received
        )
    logger.info(
        "supertonic_tts_call chars=%s ttfb_ms=%s total_ms=%s bytes=%s voice=%s lang=%s preview=%r",
        timing.chars,
        timing.ttfb_ms,
        timing.total_ms,
        timing.bytes_received,
        opts.voice,
        opts.lang,
        timing.text_preview,
    )
    return data, timing


async def _fetch_audio_chunks(
    *,
    session: aiohttp.ClientSession,
    opts: _TTSOptions,
    text: str,
    conn_options: APIConnectOptions,
    skip_wav_header: bool = False,
    pipeline: TurnPipelineTracker | None = None,
) -> tuple[list[bytes], TtsCallTiming | None]:
    text = text.strip()
    if not text:
        return [], None

    endpoint = f"{opts.base_url}/v1/tts"
    payload = _build_payload(opts, text)
    t0 = time.perf_counter()

    try:
        async with session.post(
            endpoint,
            json=payload,
            timeout=aiohttp.ClientTimeout(
                total=300,
                sock_connect=conn_options.timeout,
            ),
        ) as resp:
            if resp.status != 200:
                body = (await resp.text())[:300]
                raise APIStatusError(
                    message=f"Supertonic TTS HTTP {resp.status}: {body}",
                    status_code=resp.status,
                    request_id=None,
                    body=body,
                )
            data_parts: list[bytes] = []
            async for chunk in resp.content.iter_any():
                if not chunk:
                    continue
                if pipeline:
                    pipeline.mark_tts_first_server_chunk()
                data_parts.append(chunk)
            data = b"".join(data_parts)
    except asyncio.TimeoutError:
        raise APITimeoutError() from None
    except aiohttp.ClientError as e:
        raise APIConnectionError() from e

    total_ms = int((time.perf_counter() - t0) * 1000)
    ttfb_ms = (
        int((pipeline.t_tts_first_server_chunk - t0) * 1000)
        if pipeline and pipeline.t_tts_first_server_chunk
        else total_ms
    )
    trim = os.environ.get("VOICE_AGENT_TTS_TRIM_PHRASE_SILENCE", "true").lower() != "false"
    if trim or skip_wav_header:
        data = prepare_phrase_pcm(
            data,
            sample_rate=SAMPLE_RATE,
            strip_wav_header=skip_wav_header,
            trim_leading=trim and skip_wav_header,
            trim_trailing=trim,
        )
    elif skip_wav_header:
        data = strip_leading_wav_header(data)

    chunks_out = [
        data[i : i + EMIT_CHUNK_BYTES] for i in range(0, len(data), EMIT_CHUNK_BYTES)
    ]
    timing = TtsCallTiming(
        text_preview=text[:80],
        chars=len(text),
        ttfb_ms=max(0, ttfb_ms),
        total_ms=total_ms,
        bytes_received=len(data),
    )
    if pipeline:
        pipeline.mark_tts_http_complete(
            timing_ms=timing.total_ms, bytes_received=timing.bytes_received
        )
    logger.info(
        "supertonic_tts_call chars=%s ttfb_ms=%s total_ms=%s bytes=%s voice=%s lang=%s preview=%r",
        timing.chars,
        timing.ttfb_ms,
        timing.total_ms,
        timing.bytes_received,
        opts.voice,
        opts.lang,
        timing.text_preview,
    )
    return chunks_out, timing


def build_supertonic_tts(
    overrides: dict[str, str | float] | None = None,
    *,
    on_timing: Callable[[TtsCallTiming], None] | None = None,
    pipeline_tracker: TurnPipelineTracker | None = None,
    on_llm_first_token: Callable[[], None] | None = None,
) -> TTS:
    cfg = overrides or {}
    max_chunk = cfg.get("max_chunk_length")
    return TTS(
        base_url=_tts_env("TTS_BASE_URL", "CHATTERBOX_TTS_URL", DEFAULT_BASE_URL),
        voice=(
            str(cfg.get("voice") or cfg.get("predefined_voice_id"))
            if (cfg.get("voice") or cfg.get("predefined_voice_id"))
            else None
        ),
        lang=str(cfg["lang"]) if cfg.get("lang") else None,
        model=_tts_env("TTS_MODEL", "", DEFAULT_MODEL),
        max_chunk_length=int(max_chunk) if max_chunk else _env_int("TTS_MAX_CHUNK_LENGTH", 0) or None,
        min_text_chars=_env_int("VOICE_AGENT_TTS_MIN_TEXT_CHARS", 8),
        max_text_chars=_env_int("VOICE_AGENT_TTS_MAX_TEXT_CHARS", 2000),
        on_timing=on_timing,
        pipeline_tracker=pipeline_tracker,
        on_llm_first_token=on_llm_first_token,
    )
