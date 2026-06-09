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
from agent.pipeline.tts_audio_utils import (
    is_json_response,
    is_wav,
    parse_wav_info,
    prepare_phrase_pcm,
    strip_leading_wav_header,
)
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
    provider_voice_id: str = ""
    fallback_voice: str = ""
    voice_profile_id: str = ""
    voice_mode: str = "preset"
    synth_endpoint: str = "/v1/tts"


def _is_custom_voice(opts: _TTSOptions) -> bool:
    return opts.voice_mode == "custom" and bool(opts.provider_voice_id.strip())


def _effective_voice(opts: _TTSOptions) -> str:
    if _is_custom_voice(opts):
        return opts.provider_voice_id
    return opts.fallback_voice or opts.voice


def _build_payload(opts: _TTSOptions, text: str) -> dict:
    payload: dict = {
        "text": text,
        "lang": opts.lang,
        "response_format": opts.response_format or "wav",
    }
    if opts.voice_mode == "custom":
        voice_id = (opts.provider_voice_id or "").strip()
        if not voice_id:
            raise APIStatusError(
                message=(
                    "custom voice_mode requires provider_voice_id but none was configured "
                    f"(voice_profile_id={opts.voice_profile_id or '-'})"
                ),
                status_code=400,
                request_id=None,
                body=None,
            )
        payload["voice_id"] = voice_id
    else:
        payload["voice"] = opts.fallback_voice or opts.voice or DEFAULT_VOICE
    if opts.max_chunk_length:
        payload["max_chunk_length"] = opts.max_chunk_length
    return payload


def _header_get(headers, name: str) -> str:
    return headers.get(name) or headers.get(name.lower()) or headers.get(name.title()) or ""


def _synth_endpoint(opts: _TTSOptions) -> str:
    path = (opts.synth_endpoint or "/v1/tts").strip()
    if not path.startswith("/"):
        path = f"/{path}"
    return f"{opts.base_url}{path}"


def _log_tts_request(
    *,
    opts: _TTSOptions,
    endpoint: str,
    payload: dict,
) -> None:
    logger.info(
        "supertonic_tts_request endpoint=%s tts_base_url=%s voice_mode=%s "
        "voice_profile_id=%s provider_voice_id=%s fallback_voice=%s "
        "payload_keys=%s voice=%s voice_id=%s lang=%s response_format=%s text_chars=%s",
        endpoint,
        opts.base_url,
        opts.voice_mode,
        opts.voice_profile_id or "-",
        opts.provider_voice_id or "-",
        opts.fallback_voice or opts.voice,
        sorted(payload.keys()),
        payload.get("voice", "-"),
        payload.get("voice_id", "-"),
        opts.lang,
        payload.get("response_format"),
        len(str(payload.get("text", ""))),
    )


def _validate_audio_data(
    data: bytes,
    *,
    opts: _TTSOptions,
    text: str,
    resp_status: int,
    content_type: str,
    voice_source: str = "",
) -> None:
    if data:
        return
    voice = _effective_voice(opts)
    raise APIStatusError(
        message=(
            f"RunPod TTS returned empty audio for voice_id={voice!r} "
            f"text={text[:120]!r} voice_profile_id={opts.voice_profile_id or '-'} "
            f"status={resp_status} content_type={content_type or '-'} "
            f"voice_source={voice_source or '-'}"
        ),
        status_code=resp_status or 502,
        request_id=None,
        body=None,
    )


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
        synth_path = os.environ.get("TTS_SYNTH_ENDPOINT", "/v1/tts").strip() or "/v1/tts"
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
            synth_endpoint=synth_path,
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
        if not chunks:
            raise _empty_chunks_error(self._tts._opts, self._input_text)
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
            pipeline.mark_llm_complete()
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
                pipeline.mark_llm_complete()
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
            if not chunks:
                raise _empty_chunks_error(self._tts._opts, full_text)
            await self._play_chunks(chunks, output_emitter, mark_started=True)
            if timing and self._tts._on_timing:
                self._tts._on_timing(timing)
        finally:
            output_emitter.end_segment()


def _empty_chunks_error(opts: _TTSOptions, text: str) -> APIStatusError:
    return APIStatusError(
        message=(
            f"TTS produced zero audio chunks for text={text[:120]!r} "
            f"voice_mode={opts.voice_mode} voice_profile_id={opts.voice_profile_id or '-'} "
            f"provider_voice_id={opts.provider_voice_id or '-'} "
            f"preset_voice={opts.fallback_voice or opts.voice or '-'} "
            "(decoded frame count=0 or WAV body empty after processing)"
        ),
        status_code=502,
        request_id=None,
        body=None,
    )


async def _download_tts_wav(
    *,
    session: aiohttp.ClientSession,
    opts: _TTSOptions,
    text: str,
    conn_options: APIConnectOptions,
    pipeline: TurnPipelineTracker | None,
) -> tuple[bytes, int, str, str]:
    endpoint = _synth_endpoint(opts)
    payload = _build_payload(opts, text)
    _log_tts_request(opts=opts, endpoint=endpoint, payload=payload)
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
            content_type = _header_get(resp.headers, "content-type")
            voice_source = _header_get(resp.headers, "X-TTS-Voice-Source")
            voice_id_hdr = _header_get(resp.headers, "X-TTS-Voice-Id")
            gen_ms = _header_get(resp.headers, "X-TTS-Gen-Ms")
            if resp.status != 200:
                body = (await resp.text())[:500]
                logger.error(
                    "supertonic_tts_error status=%s content_type=%s body=%s "
                    "voice=%s voice_id=%s voice_source=%s",
                    resp.status,
                    content_type,
                    body,
                    payload.get("voice", "-"),
                    payload.get("voice_id", "-"),
                    voice_source or "-",
                )
                raise APIStatusError(
                    message=f"RunPod TTS HTTP {resp.status}: {body}",
                    status_code=resp.status,
                    request_id=None,
                    body=body,
                )
            data_parts: list[bytes] = []
            async for chunk in resp.content.iter_any():
                if not chunk:
                    continue
                if pipeline and not pipeline.t_tts_first_server_chunk:
                    pipeline.mark_tts_first_server_chunk()
                data_parts.append(chunk)
            data = b"".join(data_parts)
            total_ms = int((time.perf_counter() - t0) * 1000)

            if not data:
                raise APIStatusError(
                    message="empty TTS audio response from RunPod",
                    status_code=502,
                    request_id=None,
                    body=None,
                )
            if is_json_response(data) or "json" in content_type.lower():
                detail = data.decode("utf-8", errors="replace")[:500]
                logger.error(
                    "supertonic_tts_json_error content_type=%s body=%s voice_id=%s",
                    content_type,
                    detail,
                    payload.get("voice_id", "-"),
                )
                raise APIStatusError(
                    message=f"RunPod TTS returned JSON instead of WAV: {detail}",
                    status_code=502,
                    request_id=None,
                    body=detail,
                )

            wav_info = parse_wav_info(data)
            logger.info(
                "supertonic_tts_response status=%s content_type=%s bytes=%s total_ms=%s "
                "voice=%s voice_id=%s X-TTS-Voice-Id=%s X-TTS-Voice-Source=%s "
                "X-TTS-Gen-Ms=%s wav_valid=%s sample_rate=%s channels=%s "
                "duration_sec=%.3f pcm_frames=%s",
                resp.status,
                content_type,
                len(data),
                total_ms,
                payload.get("voice", "-"),
                payload.get("voice_id", "-"),
                voice_id_hdr or "-",
                voice_source or "-",
                gen_ms or "-",
                wav_info["valid"],
                wav_info["sample_rate"],
                wav_info["channels"],
                wav_info["duration_sec"],
                wav_info["pcm_frames"],
            )
            if not wav_info["valid"]:
                raise APIStatusError(
                    message=(
                        f"RunPod TTS response is not a valid WAV "
                        f"(bytes={len(data)} content_type={content_type})"
                    ),
                    status_code=502,
                    request_id=None,
                    body=None,
                )
            _validate_audio_data(
                data,
                opts=opts,
                text=text,
                resp_status=resp.status,
                content_type=content_type,
                voice_source=voice_source,
            )
            return data, total_ms, content_type, voice_source
    except asyncio.TimeoutError:
        raise APITimeoutError() from None
    except aiohttp.ClientError as e:
        raise APIConnectionError() from e


async def _fetch_wav(
    *,
    session: aiohttp.ClientSession,
    opts: _TTSOptions,
    text: str,
    conn_options: APIConnectOptions,
    pipeline: TurnPipelineTracker | None = None,
) -> tuple[bytes, TtsCallTiming | None]:
    text = text.strip()
    if not text:
        return b"", None

    t0 = time.perf_counter()
    data, total_ms, _content_type, _voice_source = await _download_tts_wav(
        session=session,
        opts=opts,
        text=text,
        conn_options=conn_options,
        pipeline=pipeline,
    )

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
        "supertonic_tts_call chars=%s ttfb_ms=%s total_ms=%s bytes=%s "
        "voice_mode=%s request_voice=%s lang=%s preview=%r",
        timing.chars,
        timing.ttfb_ms,
        timing.total_ms,
        timing.bytes_received,
        opts.voice_mode,
        _effective_voice(opts),
        opts.lang,
        timing.text_preview,
    )
    return data, timing


async def _fetch_phrase_body(
    *,
    session: aiohttp.ClientSession,
    opts: _TTSOptions,
    text: str,
    conn_options: APIConnectOptions,
    pipeline: TurnPipelineTracker | None = None,
) -> tuple[bytes, TtsCallTiming | None]:
    """Download full WAV body for one phrase (trim/slice applied by phrase playback)."""
    return await _fetch_wav(
        session=session,
        opts=opts,
        text=text,
        conn_options=conn_options,
        pipeline=pipeline,
    )


async def _fetch_audio_chunks(
    *,
    session: aiohttp.ClientSession,
    opts: _TTSOptions,
    text: str,
    conn_options: APIConnectOptions,
    skip_wav_header: bool = False,
    pipeline: TurnPipelineTracker | None = None,
) -> tuple[list[bytes], TtsCallTiming | None]:
    data, timing = await _fetch_wav(
        session=session,
        opts=opts,
        text=text,
        conn_options=conn_options,
        pipeline=pipeline,
    )
    if not data:
        logger.error(
            "supertonic_tts_empty_body voice_mode=%s voice=%s voice_id=%s text=%r",
            opts.voice_mode,
            opts.fallback_voice or opts.voice,
            opts.provider_voice_id or "-",
            text[:80],
        )
        return [], timing

    wav_before = parse_wav_info(data)
    trim = os.environ.get("VOICE_AGENT_TTS_TRIM_PHRASE_SILENCE", "true").lower() != "false"

    if skip_wav_header:
        # Phrase/PCM mode: strip WAV header then optionally trim PCM silence.
        data = prepare_phrase_pcm(
            data,
            sample_rate=SAMPLE_RATE,
            strip_wav_header=True,
            trim_leading=trim,
            trim_trailing=trim,
        )
    elif not is_wav(data):
        raise APIStatusError(
            message=f"TTS response is not WAV after download (bytes={len(data)})",
            status_code=502,
            request_id=None,
            body=None,
        )
    # Full WAV mode (LiveKit audio/wav emitter): keep RIFF header intact — do not trim.

    chunks_out = [
        data[i : i + EMIT_CHUNK_BYTES] for i in range(0, len(data), EMIT_CHUNK_BYTES)
    ]
    pushed_bytes = sum(len(c) for c in chunks_out)
    logger.info(
        "supertonic_tts_chunks voice_mode=%s skip_wav_header=%s wav_before_valid=%s "
        "wav_before_frames=%s output_bytes=%s chunk_count=%s pushed_frame_chunks=%s",
        opts.voice_mode,
        skip_wav_header,
        wav_before["valid"],
        wav_before["pcm_frames"],
        len(data),
        len(chunks_out),
        len([c for c in chunks_out if c]),
    )
    if not chunks_out or pushed_bytes == 0:
        logger.error(
            "supertonic_tts_zero_chunks decoded_pcm_frames=%s output_bytes=%s",
            wav_before["pcm_frames"],
            len(data),
        )

    if timing:
        timing = TtsCallTiming(
            text_preview=timing.text_preview,
            chars=timing.chars,
            ttfb_ms=timing.ttfb_ms,
            total_ms=timing.total_ms,
            bytes_received=len(data),
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
    preset_voice = (
        str(cfg.get("voice") or cfg.get("predefined_voice_id"))
        if (cfg.get("voice") or cfg.get("predefined_voice_id"))
        else None
    )
    fallback = str(cfg.get("fallback_voice") or preset_voice or DEFAULT_VOICE)
    provider_voice_id = str(cfg.get("provider_voice_id") or cfg.get("voice_id") or "")
    voice_profile_id = str(cfg.get("voice_profile_id") or "")
    voice_mode = str(cfg.get("voice_mode") or ("custom" if provider_voice_id else "preset"))

    tts = TTS(
        base_url=_tts_env("TTS_BASE_URL", "CHATTERBOX_TTS_URL", DEFAULT_BASE_URL),
        voice=preset_voice,
        lang=str(cfg["lang"]) if cfg.get("lang") else None,
        model=_tts_env("TTS_MODEL", "", DEFAULT_MODEL),
        max_chunk_length=int(max_chunk) if max_chunk else _env_int("TTS_MAX_CHUNK_LENGTH", 0) or None,
        min_text_chars=_env_int("VOICE_AGENT_TTS_MIN_TEXT_CHARS", 8),
        max_text_chars=_env_int("VOICE_AGENT_TTS_MAX_TEXT_CHARS", 2000),
        on_timing=on_timing,
        pipeline_tracker=pipeline_tracker,
        on_llm_first_token=on_llm_first_token,
    )
    tts._opts.provider_voice_id = provider_voice_id
    tts._opts.fallback_voice = fallback
    tts._opts.voice_profile_id = voice_profile_id
    tts._opts.voice_mode = voice_mode
    if not tts._opts.voice:
        tts._opts.voice = fallback
    logger.info(
        "supertonic_tts_init voice_mode=%s voice_profile_id=%s provider_voice_id=%s "
        "preset_voice=%s fallback_voice=%s lang=%s base_url=%s",
        voice_mode,
        voice_profile_id or "-",
        provider_voice_id or "-",
        tts._opts.voice,
        fallback,
        tts._opts.lang,
        tts._opts.base_url,
    )
    return tts
