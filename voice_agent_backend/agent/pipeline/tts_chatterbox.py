"""
DEPRECATED — Chatterbox multilingual HTTP TTS (RunPod).

Replaced by agent.pipeline.tts_supertonic (Supertonic-3 local server).
Kept for reference; entrypoint no longer imports this module.

Chatterbox-specific features NOT used with Supertonic:
  - predefined_voice_id (.wav files), split_text, chunk_size (text), stream flag
  - exaggeration / temperature / cfg_weight
  - paralinguistic tags [laugh], [sigh], etc. (see prompts.py)
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable
from dataclasses import dataclass, replace

import aiohttp

from agent.observability.latency import TtsCallTiming
from agent.pipeline.tts_audio_utils import prepare_phrase_pcm, strip_leading_wav_header
from agent.pipeline.tts_http_pool import ensure_chatterbox_http_session
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

SAMPLE_RATE = 24000
NUM_CHANNELS = 1
WAV_HEADER_BYTES = 44
DEFAULT_BASE_URL = "https://hzzc2ch2wg5sjm-8000.proxy.runpod.net"
DEFAULT_VOICE_ID = "Adrian.wav"
# Server text chunk_size — smaller → earlier first audio byte per request.
DEFAULT_CHUNK_SIZE = 40
DEFAULT_MIN_TEXT_CHARS = 20
DEFAULT_MAX_TEXT_CHARS = 80

logger = logging.getLogger("agent.pipeline.tts_chatterbox")

StreamingTextChunker = SentenceTextChunker


def _normalize_base_url(url: str) -> str:
    u = url.strip().rstrip("/")
    if u.endswith("/tts"):
        u = u[: -len("/tts")]
    return u.rstrip("/")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float, override: float | None) -> float:
    if override is not None:
        return float(override)
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


@dataclass
class _TTSOptions:
    base_url: str
    voice_id: str
    output_format: str
    chunk_size: int
    split_text: bool
    stream: bool
    seed: int
    sample_rate: int
    min_text_chars: int
    max_text_chars: int
    exaggeration: float
    temperature: float
    cfg_weight: float


def _build_payload(opts: _TTSOptions, text: str) -> dict:
    return {
        "text": text,
        "voice_mode": "predefined",
        "predefined_voice_id": opts.voice_id,
        "output_format": opts.output_format,
        "split_text": opts.split_text,
        "chunk_size": opts.chunk_size,
        "stream": opts.stream,
        "seed": opts.seed,
        "exaggeration": opts.exaggeration,
        "temperature": opts.temperature,
        "cfg_weight": opts.cfg_weight,
    }


class TTS(tts.TTS):
    def __init__(
        self,
        *,
        base_url: str | None = None,
        voice_id: str | None = None,
        output_format: str = "wav",
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        split_text: bool = False,
        stream: bool = True,
        seed: int = 0,
        sample_rate: int = SAMPLE_RATE,
        min_text_chars: int = DEFAULT_MIN_TEXT_CHARS,
        max_text_chars: int = DEFAULT_MAX_TEXT_CHARS,
        exaggeration: float | None = None,
        temperature: float | None = None,
        cfg_weight: float | None = None,
        http_session: aiohttp.ClientSession | None = None,
        on_timing: Callable[[TtsCallTiming], None] | None = None,
    ) -> None:
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=True),
            sample_rate=sample_rate,
            num_channels=NUM_CHANNELS,
        )
        self._opts = _TTSOptions(
            base_url=_normalize_base_url(
                base_url or os.environ.get("CHATTERBOX_TTS_URL") or DEFAULT_BASE_URL
            ),
            voice_id=voice_id
            or os.environ.get("VOICE_AGENT_CHATTERBOX_VOICE_ID")
            or DEFAULT_VOICE_ID,
            output_format=output_format,
            chunk_size=chunk_size,
            split_text=split_text,
            stream=stream,
            seed=seed,
            sample_rate=sample_rate,
            min_text_chars=min_text_chars,
            max_text_chars=max_text_chars,
            exaggeration=_env_float("VOICE_AGENT_CHATTERBOX_EXAGGERATION", 0.65, exaggeration),
            temperature=_env_float("VOICE_AGENT_CHATTERBOX_TEMPERATURE", 0.8, temperature),
            cfg_weight=_env_float("VOICE_AGENT_CHATTERBOX_CFG_WEIGHT", 0.45, cfg_weight),
        )
        self._session = http_session
        self._on_timing = on_timing

    @property
    def model(self) -> str:
        return "chatterbox"

    @property
    def provider(self) -> str:
        return "Chatterbox"

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Process-local keep-alive pool (warmed once per LiveKit job process)."""
        if self._session is not None and not self._session.closed:
            return self._session
        self._session = await ensure_chatterbox_http_session()
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
        # Shared pool; do not close the session (other TTS streams may still use it).
        self._session = None


class ChunkedStream(tts.ChunkedStream):
    def __init__(
        self,
        *,
        tts: TTS,
        input_text: str,
        conn_options: APIConnectOptions,
    ) -> None:
        super().__init__(tts=tts, input_text=input_text, conn_options=conn_options)
        self._tts = tts
        # Server-side split_text chunks by chunk_size and causes mid-sentence pauses.
        self._opts = replace(tts._opts, split_text=False)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._opts.sample_rate,
            num_channels=NUM_CHANNELS,
            mime_type="audio/wav",
            stream=False,
        )
        await _stream_audio_chunks(
            session=await self._tts._ensure_session(),
            opts=self._opts,
            text=self._input_text,
            conn_options=self._conn_options,
            output_emitter=output_emitter,
            on_first_audio=self._mark_started,
            skip_wav_header=False,
            on_timing=self._tts._on_timing,
        )
        output_emitter.flush()


class SynthesizeStream(tts.SynthesizeStream):
    """
    One assistant reply → one POST /tts on the call's keep-alive connection.

    LLM tokens are buffered until the stream ends, then sent in a single request so
    we do not open a new TCP/TLS handshake per text chunk (handshake happens once
    per call at job warmup).
    """

    def __init__(self, *, tts: TTS, conn_options: APIConnectOptions) -> None:
        super().__init__(tts=tts, conn_options=conn_options)
        self._tts = tts
        self._opts = replace(tts._opts, split_text=False)

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=self._opts.sample_rate,
            num_channels=NUM_CHANNELS,
            mime_type="audio/wav",
            stream=True,
        )

        parts: list[str] = []
        async for data in self._input_ch:
            if isinstance(data, self._FlushSentinel):
                continue
            parts.append(str(data))

        full_text = "".join(parts).strip()
        segment_id = utils.shortuuid()
        output_emitter.start_segment(segment_id=segment_id)
        try:
            if not full_text:
                return
            logger.info(
                "chatterbox_tts_single_request chars=%s (one POST per reply, reused connection)",
                len(full_text),
            )
            await _stream_audio_chunks(
                session=await self._tts._ensure_session(),
                opts=self._opts,
                text=full_text,
                conn_options=self._conn_options,
                output_emitter=output_emitter,
                on_first_audio=self._mark_started,
                skip_wav_header=False,
                on_timing=self._tts._on_timing,
            )
        finally:
            output_emitter.end_segment()


async def _stream_audio_chunks(
    *,
    session: aiohttp.ClientSession,
    opts: _TTSOptions,
    text: str,
    conn_options: APIConnectOptions,
    output_emitter: tts.AudioEmitter,
    on_first_audio: Callable[[], None],
    skip_wav_header: bool = False,
    on_timing: Callable[[TtsCallTiming], None] | None = None,
) -> TtsCallTiming | None:
    text = text.strip()
    if not text:
        return None

    endpoint = f"{opts.base_url}/tts"
    payload = _build_payload(opts, text)
    t0 = time.perf_counter()
    ttfb_ms = -1
    bytes_received = 0

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
                    message=f"Chatterbox TTS HTTP {resp.status}: {body}",
                    status_code=resp.status,
                    request_id=None,
                    body=body,
                )

            first_audio = True
            first_chunk = True
            async for chunk in resp.content.iter_any():
                if not chunk:
                    continue
                if first_chunk and skip_wav_header:
                    chunk = strip_leading_wav_header(chunk)
                    first_chunk = False
                elif first_chunk:
                    first_chunk = False
                if not chunk:
                    continue
                if first_audio:
                    first_audio = False
                    ttfb_ms = int((time.perf_counter() - t0) * 1000)
                    on_first_audio()
                bytes_received += len(chunk)
                output_emitter.push(chunk)
    except asyncio.TimeoutError:
        raise APITimeoutError() from None
    except aiohttp.ClientError as e:
        raise APIConnectionError() from e

    total_ms = int((time.perf_counter() - t0) * 1000)
    timing = TtsCallTiming(
        text_preview=text[:80],
        chars=len(text),
        ttfb_ms=max(0, ttfb_ms),
        total_ms=total_ms,
        bytes_received=bytes_received,
    )
    logger.info(
        "chatterbox_tts_call chars=%s ttfb_ms=%s total_ms=%s bytes=%s preview=%r",
        timing.chars,
        timing.ttfb_ms,
        timing.total_ms,
        timing.bytes_received,
        timing.text_preview,
    )
    if on_timing:
        on_timing(timing)
    return timing


def build_chatterbox_tts(
    overrides: dict[str, str | float] | None = None,
    *,
    on_timing: Callable[[TtsCallTiming], None] | None = None,
) -> TTS:
    cfg = overrides or {}
    return TTS(
        base_url=os.environ.get("CHATTERBOX_TTS_URL"),
        voice_id=(
            str(cfg["predefined_voice_id"])
            if cfg.get("predefined_voice_id")
            else os.environ.get("VOICE_AGENT_CHATTERBOX_VOICE_ID")
        ),
        output_format=os.environ.get("VOICE_AGENT_CHATTERBOX_OUTPUT_FORMAT", "wav"),
        chunk_size=_env_int("VOICE_AGENT_CHATTERBOX_CHUNK_SIZE", DEFAULT_CHUNK_SIZE),
        split_text=os.environ.get("VOICE_AGENT_CHATTERBOX_SPLIT_TEXT", "false").lower()
        == "true",
        stream=os.environ.get("VOICE_AGENT_CHATTERBOX_STREAM", "true").lower() != "false",
        seed=_env_int("VOICE_AGENT_CHATTERBOX_SEED", 0),
        min_text_chars=_env_int(
            "VOICE_AGENT_CHATTERBOX_MIN_TEXT_CHARS", DEFAULT_MIN_TEXT_CHARS
        ),
        max_text_chars=_env_int(
            "VOICE_AGENT_CHATTERBOX_MAX_TEXT_CHARS", DEFAULT_MAX_TEXT_CHARS
        ),
        exaggeration=cfg.get("exaggeration"),  # type: ignore[arg-type]
        temperature=cfg.get("temperature"),  # type: ignore[arg-type]
        cfg_weight=cfg.get("cfg_weight"),  # type: ignore[arg-type]
        on_timing=on_timing,
    )
