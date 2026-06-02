"""Self-hosted Faster Whisper / WhisperLiveKit STT over WebSocket."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import time
import weakref
from dataclasses import dataclass, field
from typing import Any

import aiohttp

from livekit import rtc
from livekit.agents import (
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    APITimeoutError,
    stt,
    utils,
)
from livekit.agents.language import LanguageCode
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, NOT_GIVEN, NotGivenOr
from livekit.agents.utils import AudioBuffer, is_given

from agent.observability.pipeline_events import log_pipeline_event
from agent.observability.pipeline_latency import TurnPipelineTracker
from agent.pipeline.stt_config import (
    faster_whisper_sample_rate,
    faster_whisper_send_config,
    faster_whisper_streaming_enabled,
    faster_whisper_timeout_seconds,
    get_stt_provider,
    resolve_faster_whisper_language,
    resolve_faster_whisper_ws_url,
    stt_provider_label,
)
from agent.pipeline.stt_partial_finalize import PartialFinalizeController
from agent.pipeline.user_turn_streaming import final_stability_ms
from agent.pipeline.stt_transcript_utils import (
    clean_transcript,
    extract_stt_delta,
    is_duplicate_transcript,
    transcript_extends_prior,
    min_final_transcript_chars,
)

logger = logging.getLogger("agent.pipeline.stt_faster_whisper")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


@dataclass
class FasterWhisperSTTOptions:
    ws_url: str
    language: str
    sample_rate: int
    interim_results: bool
    timeout_s: float
    send_config: bool


@dataclass
class _WlkStreamState:
    use_pcm: bool = True
    line_count: int = 0
    committed_texts: list[str] = field(default_factory=list)
    last_buffer: str = ""
    last_partial_emitted: str = ""
    last_final_emitted: str = ""
    ready_to_stop: bool = False
    utterance_final_emitted: bool = False
    audio_frames_sent: int = 0
    audio_bytes_sent: int = 0
    logged_audio_format: bool = False
    ws_msgs_seen: int = 0


class _WsHolder:
    """Mutable WebSocket ref so send loop survives per-utterance reconnects."""

    def __init__(self) -> None:
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.lock = asyncio.Lock()

    async def send_bytes(self, data: bytes) -> None:
        async with self.lock:
            if self.ws is None or self.ws.closed:
                return
            await self.ws.send_bytes(data)


def build_faster_whisper_stt(
    conversation_language: str = "en",
    *,
    http_session: aiohttp.ClientSession | None = None,
) -> STT:
    lang = resolve_faster_whisper_language(conversation_language)
    return STT(
        ws_url=resolve_faster_whisper_ws_url(conversation_language),
        language=lang,
        sample_rate=faster_whisper_sample_rate(),
        interim_results=faster_whisper_streaming_enabled(),
        timeout_s=faster_whisper_timeout_seconds(),
        send_config=faster_whisper_send_config(),
        http_session=http_session,
    )


class STT(stt.STT):
    def __init__(
        self,
        *,
        ws_url: str,
        language: str = "en",
        sample_rate: int = 16000,
        interim_results: bool = True,
        timeout_s: float = 30.0,
        send_config: bool = False,
        http_session: aiohttp.ClientSession | None = None,
    ) -> None:
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True,
                interim_results=interim_results,
                diarization=False,
            )
        )
        self._opts = FasterWhisperSTTOptions(
            ws_url=ws_url,
            language=language,
            sample_rate=sample_rate,
            interim_results=interim_results,
            timeout_s=timeout_s,
            send_config=send_config,
        )
        self._session = http_session
        self._streams: weakref.WeakSet[SpeechStream] = weakref.WeakSet()
        self._provider_id = get_stt_provider()
        self._performance_tracker: TurnPipelineTracker | None = None
        self._committed_final_ref: list[str] | None = None

    def bind_performance_tracker(
        self, tracker: TurnPipelineTracker | None
    ) -> None:
        self._performance_tracker = tracker

    def bind_committed_final_ref(self, ref: list[str]) -> None:
        """Shared with entrypoint TurnCommitController.last_committed_final."""
        self._committed_final_ref = ref

    @property
    def model(self) -> str:
        return "faster-whisper"

    @property
    def provider(self) -> str:
        return stt_provider_label(self._provider_id)

    async def _recognize_impl(
        self,
        buffer: AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        lang = language if is_given(language) else self._opts.language
        stream = self.stream(language=lang, conn_options=conn_options)
        try:
            for frame in buffer:
                stream.push_frame(frame)
            stream.flush()
            stream.end_input()
            text = ""
            async for ev in stream:
                if ev.type == stt.SpeechEventType.FINAL_TRANSCRIPT and ev.alternatives:
                    text = ev.alternatives[0].text
                    break
            if not text.strip():
                raise APIConnectionError("faster-whisper returned no transcript")
            return stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[
                    stt.SpeechData(language=LanguageCode(lang), text=text.strip())
                ],
            )
        finally:
            await stream.aclose()

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> SpeechStream:
        lang = language if is_given(language) else self._opts.language
        opts = FasterWhisperSTTOptions(
            ws_url=resolve_faster_whisper_ws_url(lang),
            language=lang,
            sample_rate=self._opts.sample_rate,
            interim_results=self._opts.interim_results,
            timeout_s=self._opts.timeout_s,
            send_config=self._opts.send_config,
        )
        stream = SpeechStream(
            stt=self,
            opts=opts,
            conn_options=conn_options,
            http_session=self._session,
        )
        self._streams.add(stream)
        return stream

    def update_options(self, *, language: NotGivenOr[str] = NOT_GIVEN) -> None:
        if is_given(language):
            self._opts.language = str(language)
            self._opts.ws_url = resolve_faster_whisper_ws_url(self._opts.language)
        for stream in self._streams:
            stream.update_options(language=language)

    async def aclose(self) -> None:
        pass


class SpeechStream(stt.SpeechStream):
    def __init__(
        self,
        *,
        stt: STT,
        opts: FasterWhisperSTTOptions,
        conn_options: APIConnectOptions,
        http_session: aiohttp.ClientSession | None,
    ) -> None:
        if not http_session:
            raise ValueError("Faster Whisper STT requires a shared aiohttp ClientSession")
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=opts.sample_rate)
        self._opts = opts
        self._session = http_session
        self._speaking = False
        self._reconnect_event = asyncio.Event()
        self._wlk = _WlkStreamState()
        self._last_interim = ""
        self._finalize_after_flush_task: asyncio.Task[None] | None = None
        self._final_stability_task: asyncio.Task[None] | None = None
        self._final_stability_gen = 0
        self._partial_finalize = PartialFinalizeController(
            emit_final=lambda t: self._emit_final_transcript(
                t, source="partial_finalize_timeout"
            ),
            get_text=self._best_utterance_text,
            is_already_final=lambda: self._wlk.utterance_final_emitted,
            min_chars=min_final_transcript_chars(),
        )

    def update_options(self, *, language: NotGivenOr[str] = NOT_GIVEN) -> None:
        if is_given(language):
            self._opts.language = str(language)
            self._opts.ws_url = resolve_faster_whisper_ws_url(self._opts.language)
            self._reconnect_event.set()

    async def _connect_ws(self, holder: _WsHolder) -> aiohttp.ClientWebSocketResponse:
        timeout = aiohttp.ClientTimeout(total=self._opts.timeout_s)
        try:
            ws = await self._session.ws_connect(
                self._opts.ws_url,
                timeout=timeout,
                heartbeat=30.0,
                autoping=True,
            )
        except asyncio.TimeoutError as e:
            raise APITimeoutError() from e
        except aiohttp.ClientError as e:
            raise APIConnectionError(str(e)) from e

        # Publish WS before waiting for config so mic audio is not dropped.
        holder.ws = ws

        try:
            msg = await asyncio.wait_for(ws.receive(), timeout=self._opts.timeout_s)
        except asyncio.TimeoutError as e:
            await ws.close()
            raise APITimeoutError("timed out waiting for WhisperLiveKit config") from e

        if msg.type != aiohttp.WSMsgType.TEXT:
            await ws.close()
            raise APIConnectionError(
                f"expected WhisperLiveKit config JSON, got ws message type {msg.type}"
            )

        try:
            server_cfg = json.loads(msg.data)
        except json.JSONDecodeError as e:
            await ws.close()
            raise APIConnectionError(f"invalid WhisperLiveKit config: {msg.data!r}") from e

        if server_cfg.get("type") != "config":
            raise APIConnectionError(
                f"expected WhisperLiveKit config message, got: {server_cfg!r}"
            )

        self._wlk.use_pcm = bool(server_cfg.get("useAudioWorklet", True))
        logger.info(
            "whisperlivekit_config ws_url=%s use_pcm=%s mode=%s language=%s",
            self._opts.ws_url,
            self._wlk.use_pcm,
            server_cfg.get("mode"),
            self._opts.language,
        )
        log_pipeline_event(
            "STT_WS_CONNECTED",
            provider=self._stt.provider,
            ws_url=self._opts.ws_url,
            use_pcm=self._wlk.use_pcm,
            language=self._opts.language,
        )

        if self._opts.send_config:
            await ws.send_str(
                json.dumps(
                    {
                        "type": "config",
                        "language": self._opts.language,
                        "sample_rate": self._opts.sample_rate,
                    }
                )
            )

        return ws

    def _reset_utterance_state(self) -> None:
        """New WhisperLiveKit utterance after ready_to_stop (same call, new WS)."""
        self._wlk.committed_texts = []
        self._wlk.line_count = 0
        self._wlk.last_buffer = ""
        self._wlk.last_partial_emitted = ""
        self._wlk.ready_to_stop = False
        self._wlk.utterance_final_emitted = False
        self._wlk.ws_msgs_seen = 0
        self._partial_finalize.cancel(reason="utterance_reset")
        self._cancel_final_stability_timer(reason="utterance_reset")

    def _cancel_final_stability_timer(self, *, reason: str) -> None:
        self._final_stability_gen += 1
        task = self._final_stability_task
        self._final_stability_task = None
        if task and not task.done():
            task.cancel()
        if reason:
            log_pipeline_event(
                "STT_FINAL_STABILITY_TIMER_CANCELLED",
                reason=reason,
            )

    def _best_utterance_text(self) -> str:
        text = clean_transcript(_utterance_final_text(self._wlk))
        if text:
            return text
        return clean_transcript(self._last_interim)

    def _text_for_livekit(self, full_text: str) -> str:
        """Strip cumulative prefix from prior committed finals before LiveKit sees the line."""
        cleaned = clean_transcript(full_text)
        if not cleaned:
            return ""
        if self._stt._committed_final_ref is not None:
            delta = extract_stt_delta(self._stt._committed_final_ref[0], cleaned)
            return delta
        return cleaned

    def _emit_final_transcript(
        self, text: str, *, source: str, raw_preview: str = ""
    ) -> bool:
        """Emit FINAL_TRANSCRIPT so LiveKit can commit the user turn and run the LLM."""
        cleaned = clean_transcript(text)
        if not cleaned:
            return False
        emit_text = self._text_for_livekit(cleaned)
        if not emit_text:
            log_pipeline_event(
                "STT_DELTA_EMPTY_AFTER_PREFIX",
                source=source,
                text_preview=cleaned[:120],
            )
            return False
        if (
            self._wlk.utterance_final_emitted
            and self._wlk.last_final_emitted
            and not transcript_extends_prior(self._wlk.last_final_emitted, emit_text)
        ):
            log_pipeline_event(
                "DUPLICATE_FINAL_TRANSCRIPT_DROPPED",
                text_preview=cleaned[:80],
                source=source,
            )
            return False

        if (
            self._wlk.utterance_final_emitted
            and self._wlk.last_final_emitted
            and transcript_extends_prior(self._wlk.last_final_emitted, cleaned)
        ):
            log_pipeline_event(
                "STT_FINAL_SUPERSEDES_PRIOR",
                prior_preview=self._wlk.last_final_emitted[:80],
                text_preview=cleaned[:120],
                source=source,
            )
            self._cancel_final_stability_timer(reason="superseded_final")
            self._wlk.utterance_final_emitted = False

        if is_duplicate_transcript(self._wlk.last_final_emitted, emit_text):
            log_pipeline_event(
                "DUPLICATE_FINAL_TRANSCRIPT_DROPPED",
                text_preview=cleaned[:80],
                source=source,
            )
            return False

        self._partial_finalize.cancel(reason="final_scheduled")
        self._schedule_stable_final_emit(
            emit_text=emit_text,
            cleaned=cleaned,
            source=source,
            raw_preview=raw_preview,
        )
        return True

    def _schedule_stable_final_emit(
        self,
        *,
        emit_text: str,
        cleaned: str,
        source: str,
        raw_preview: str,
    ) -> None:
        self._cancel_final_stability_timer(reason="")
        self._final_stability_gen += 1
        generation = self._final_stability_gen
        delay_ms = final_stability_ms()
        log_pipeline_event(
            "STT_FINAL_STABILITY_TIMER_STARTED",
            timeout_ms=delay_ms,
            text_preview=emit_text[:80],
            source=source,
        )

        async def _run() -> None:
            if delay_ms > 0:
                await asyncio.sleep(delay_ms / 1000.0)
            if generation != self._final_stability_gen:
                return
            self._send_final_to_livekit(
                emit_text=emit_text,
                cleaned=cleaned,
                source=source,
                raw_preview=raw_preview,
            )

        self._final_stability_task = asyncio.create_task(_run())

    def _send_final_to_livekit(
        self,
        *,
        emit_text: str,
        cleaned: str,
        source: str,
        raw_preview: str,
    ) -> None:
        self._wlk.last_final_emitted = emit_text
        self._wlk.utterance_final_emitted = True
        self._last_interim = ""
        self._wlk.last_partial_emitted = ""
        log_pipeline_event(
            "STT_FINAL_TRANSCRIPT_EMITTED",
            source=source,
            text_preview=cleaned[:120],
        )
        log_pipeline_event(
            "STT_FINAL_RAW",
            text_preview=raw_preview[:120] or cleaned[:120],
            source=source,
        )
        log_pipeline_event(
            "STT_FINAL_CLEANED",
            text_preview=cleaned[:120],
            language=self._opts.language,
            source=source,
        )
        self._event_ch.send_nowait(
            stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[
                    stt.SpeechData(
                        language=LanguageCode(self._opts.language),
                        text=emit_text,
                    )
                ],
            )
        )

    async def _refine_final_after_flush(self) -> None:
        """Wait for WLK ready_to_stop; emit final on flush if none arrived yet."""
        timeout_s = max(
            0.5,
            float(_env_int("VOICE_AGENT_STT_FLUSH_FINAL_TIMEOUT_MS", 2000)) / 1000.0,
        )
        deadline = time.perf_counter() + timeout_s
        while time.perf_counter() < deadline:
            if self._wlk.ready_to_stop or self._wlk.utterance_final_emitted:
                return
            await asyncio.sleep(0.05)
        if self._wlk.utterance_final_emitted:
            return
        text = self._best_utterance_text()
        if self._emit_final_transcript(text, source="flush_timeout"):
            log_pipeline_event(
                "STT_FINAL_FORCED",
                reason="no_ready_to_stop",
                text_preview=text[:120],
            )

    def _schedule_flush_finalize(self) -> None:
        if self._finalize_after_flush_task and not self._finalize_after_flush_task.done():
            self._finalize_after_flush_task.cancel()
        self._finalize_after_flush_task = asyncio.create_task(
            self._refine_final_after_flush()
        )

    def _log_recv_preview(self, raw: str) -> None:
        self._wlk.ws_msgs_seen += 1
        if self._wlk.ws_msgs_seen <= 5:
            log_pipeline_event(
                "STT_WS_RECV",
                msg_num=self._wlk.ws_msgs_seen,
                preview=raw[:200],
                frames_sent=self._wlk.audio_frames_sent,
            )

    @utils.log_exceptions(logger=logger)
    async def _send_loop(self, holder: _WsHolder) -> None:
        """Push mic audio to the active WS for the whole call."""
        if not self._wlk.use_pcm:
            logger.warning(
                "WhisperLiveKit useAudioWorklet=false; sending PCM s16le anyway — "
                "start server with --pcm-input for best results"
            )

        samples_50ms = max(1, self._opts.sample_rate // 20)
        chunk_duration_ms = int(1000 * samples_50ms / self._opts.sample_rate)
        audio_bstream = utils.audio.AudioByteStream(
            sample_rate=self._opts.sample_rate,
            num_channels=1,
            samples_per_channel=samples_50ms,
        )
        log_every = max(1, _env_int("VOICE_AGENT_STT_AUDIO_LOG_EVERY_N_FRAMES", 100))

        async for data in self._input_ch:
            frames: list[rtc.AudioFrame] = []
            if isinstance(data, rtc.AudioFrame):
                frames.extend(audio_bstream.write(data.data.tobytes()))
            elif isinstance(data, self._FlushSentinel):
                frames.extend(audio_bstream.flush())
                await holder.send_bytes(b"")
                log_pipeline_event(
                    "STT_UTTERANCE_END_SIGNAL",
                    bytes_sent=self._wlk.audio_bytes_sent,
                    frames_sent=self._wlk.audio_frames_sent,
                )
                self._partial_finalize.cancel(reason="vad_flush")
                # LiveKit only commits user turns on FINAL_TRANSCRIPT (not interim).
                # WLK may never send ready_to_stop while the WS stays open — finalize now.
                await asyncio.sleep(0)
                emitted = self._emit_final_transcript(
                    self._best_utterance_text(),
                    source="vad_flush",
                )
                if not emitted:
                    await asyncio.sleep(0.15)
                    self._emit_final_transcript(
                        self._best_utterance_text(),
                        source="vad_flush_retry",
                    )
                self._schedule_flush_finalize()
                if self._speaking:
                    self._speaking = False
                    self._event_ch.send_nowait(
                        stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH)
                    )
                continue

            for frame in frames:
                payload = frame.data.tobytes()
                await holder.send_bytes(payload)
                self._wlk.audio_frames_sent += 1
                self._wlk.audio_bytes_sent += len(payload)
                if not self._wlk.logged_audio_format:
                    self._wlk.logged_audio_format = True
                    log_pipeline_event(
                        "AUDIO_FRAME_SENT",
                        sample_rate=frame.sample_rate,
                        channels=frame.num_channels,
                        chunk_duration_ms=chunk_duration_ms,
                        pcm_format="s16le",
                    )
                elif self._wlk.audio_frames_sent % log_every == 0:
                    log_pipeline_event(
                        "AUDIO_FRAME_SENT",
                        frames_sent=self._wlk.audio_frames_sent,
                        bytes_sent=self._wlk.audio_bytes_sent,
                    )
                if not self._speaking:
                    self._partial_finalize.cancel(reason="start_of_speech")
                    self._speaking = True
                    perf = self._stt._performance_tracker
                    if perf is not None:
                        perf.mark_stt_processing_start()
                    self._event_ch.send_nowait(
                        stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH)
                    )

    def _log_ws_message(self, msg: aiohttp.WSMessage, *, raw: str = "") -> None:
        if not _env_int("VOICE_AGENT_STT_WS_DEBUG", 0):
            return
        preview = (raw or str(msg.data))[:240]
        log_pipeline_event(
            "STT_WS_MSG",
            ws_type=str(msg.type),
            preview=preview,
            frames_sent=self._wlk.audio_frames_sent,
        )

    @utils.log_exceptions(logger=logger)
    async def _recv_loop(self, ws: aiohttp.ClientWebSocketResponse) -> str:
        """Receive until WhisperLiveKit finishes one utterance."""
        recv_timeout_s = max(5.0, float(_env_int("VOICE_AGENT_STT_WS_RECV_TIMEOUT_S", 45)))
        while True:
            try:
                msg = await asyncio.wait_for(ws.receive(), timeout=recv_timeout_s)
            except asyncio.TimeoutError:
                logger.warning(
                    "whisperlivekit_recv_idle %.0fs frames_sent=%s bytes_sent=%s",
                    recv_timeout_s,
                    self._wlk.audio_frames_sent,
                    self._wlk.audio_bytes_sent,
                )
                log_pipeline_event(
                    "STT_WS_RECV_IDLE",
                    timeout_s=recv_timeout_s,
                    frames_sent=self._wlk.audio_frames_sent,
                    bytes_sent=self._wlk.audio_bytes_sent,
                )
                continue

            if msg.type in (
                aiohttp.WSMsgType.CLOSED,
                aiohttp.WSMsgType.CLOSE,
                aiohttp.WSMsgType.CLOSING,
            ):
                if self._input_ch.closed or self._session.closed:
                    return "closed"
                raise APIStatusError(
                    message="faster-whisper websocket closed unexpectedly",
                    status_code=ws.close_code or -1,
                    body=str(msg.data)[:200],
                )

            if msg.type == aiohttp.WSMsgType.BINARY:
                raw_bin = msg.data
                if isinstance(raw_bin, (bytes, bytearray)):
                    try:
                        raw = raw_bin.decode("utf-8")
                    except UnicodeDecodeError:
                        self._log_ws_message(msg)
                        continue
                    self._log_ws_message(msg, raw=raw[:240])
                    self._log_recv_preview(raw)
                    for event in _parse_server_text(raw, self._wlk):
                        self._emit_parsed_event(event, raw_preview=raw[:200])
                    if self._wlk.ready_to_stop:
                        log_pipeline_event("STT_WLK_READY_TO_STOP")
                        return "utterance_done"
                continue

            if msg.type != aiohttp.WSMsgType.TEXT:
                self._log_ws_message(msg)
                continue

            raw = msg.data if isinstance(msg.data, str) else str(msg.data)
            self._log_ws_message(msg, raw=raw)
            self._log_recv_preview(raw)
            for event in _parse_server_text(raw, self._wlk):
                self._emit_parsed_event(event, raw_preview=raw[:200])

            if self._wlk.ready_to_stop:
                log_pipeline_event("STT_WLK_READY_TO_STOP")
                return "utterance_done"

        return "closed"

    async def _run(self) -> None:
        holder = _WsHolder()
        send_task = asyncio.create_task(self._send_loop(holder))
        try:
            while not self._input_ch.closed:
                if self._reconnect_event.is_set():
                    self._reconnect_event.clear()
                    self._opts.ws_url = resolve_faster_whisper_ws_url(self._opts.language)

                ws: aiohttp.ClientWebSocketResponse | None = None
                try:
                    ws = await self._connect_ws(holder)
                    self._report_connection_acquired(0.0, connection_reused=False)
                    reason = await self._recv_loop(ws)
                finally:
                    holder.ws = None
                    if ws is not None and not ws.closed:
                        await ws.close()

                if reason == "closed" and self._input_ch.closed:
                    break
                if reason == "utterance_done":
                    self._reset_utterance_state()
                    log_pipeline_event(
                        "STT_WLK_RECONNECT",
                        reason="next_utterance",
                        frames_sent_total=self._wlk.audio_frames_sent,
                    )
                    continue
                if self._reconnect_event.is_set():
                    continue
                break
        finally:
            if self._finalize_after_flush_task and not self._finalize_after_flush_task.done():
                self._finalize_after_flush_task.cancel()
            self._cancel_final_stability_timer(reason="stream_close")
            await self._partial_finalize.aclose()
            send_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await send_task

    def _emit_parsed_event(self, parsed: dict[str, Any], *, raw_preview: str = "") -> None:
        text = clean_transcript(parsed.get("text") or "")
        if not text:
            return

        is_final = bool(parsed.get("is_final"))

        if is_final:
            self._emit_final_transcript(
                text,
                source="wlk_ready_to_stop",
                raw_preview=raw_preview,
            )
            return

        if not self._opts.interim_results:
            return

        if is_duplicate_transcript(self._last_interim, text):
            return

        if (
            self._wlk.utterance_final_emitted
            and self._wlk.last_final_emitted
            and transcript_extends_prior(self._wlk.last_final_emitted, text)
        ):
            self._wlk.utterance_final_emitted = False
            log_pipeline_event(
                "STT_UTTERANCE_RESUMED_AFTER_FINAL",
                prior_preview=self._wlk.last_final_emitted[:80],
                text_preview=text[:120],
            )

        self._last_interim = text
        self._wlk.last_partial_emitted = text
        emit_text = self._text_for_livekit(text) or text
        perf = self._stt._performance_tracker
        if perf is not None:
            perf.record_stt_partial(text_preview=emit_text)
        log_pipeline_event(
            "STT_PARTIAL_RAW",
            text_preview=text[:120],
        )
        self._partial_finalize.on_partial(text)
        self._event_ch.send_nowait(
            stt.SpeechEvent(
                type=stt.SpeechEventType.INTERIM_TRANSCRIPT,
                alternatives=[
                    stt.SpeechData(
                        language=LanguageCode(self._opts.language), text=emit_text
                    )
                ],
            )
        )


def _lines_to_texts(lines: list[Any]) -> list[str]:
    texts: list[str] = []
    for line in lines:
        if not isinstance(line, dict):
            continue
        text = (line.get("text") or "").strip()
        speaker = line.get("speaker")
        if text and speaker != -2:
            texts.append(text)
    return texts


def _utterance_final_text(state: _WlkStreamState) -> str:
    committed = " ".join(state.committed_texts).strip()
    buf = state.last_buffer.strip()
    if buf and committed:
        if buf in committed or committed.endswith(buf):
            return committed
        return f"{committed} {buf}".strip()
    return buf or committed


def _wlk_buffer_text(data: dict[str, Any]) -> str:
    for key in (
        "buffer_transcription",
        "buffer",
        "partial",
        "unstable_text",
        "buffer_text",
    ):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def _wlk_lines(data: dict[str, Any]) -> list[Any]:
    for key in ("lines", "segments", "transcription", "committed"):
        val = data.get(key)
        if isinstance(val, list):
            return val
    return []


def _parse_wlk_message(data: dict[str, Any], state: _WlkStreamState) -> list[dict[str, Any]]:
    msg_type = str(data.get("type") or "").lower()
    if msg_type == "ready_to_stop":
        state.ready_to_stop = True
        final = clean_transcript(_utterance_final_text(state))
        if final and not is_duplicate_transcript(state.last_final_emitted, final):
            return [{"text": final, "is_final": True, "is_partial": False}]
        return []

    if msg_type in ("config", "snapshot", "diff"):
        return []

    if data.get("error"):
        logger.error("whisperlivekit_error: %s", data.get("error"))
        return []

    events: list[dict[str, Any]] = []
    lines = _wlk_lines(data)
    prev_committed = state.committed_texts
    state.committed_texts = _lines_to_texts(lines)
    state.line_count = len(lines)

    buffer = _wlk_buffer_text(data)
    state.last_buffer = buffer

    live = clean_transcript(_utterance_final_text(state))
    if live and not is_duplicate_transcript(state.last_partial_emitted, live):
        # Simulstreaming often updates `lines` without `buffer_transcription`.
        events.append({"text": live, "is_final": False, "is_partial": True})
    elif (
        state.committed_texts != prev_committed
        and state.committed_texts
        and not live
    ):
        log_pipeline_event(
            "STT_WLK_LINES_ONLY",
            line_count=state.line_count,
            committed_preview=" ".join(state.committed_texts)[:80],
        )

    status = str(data.get("status") or "")
    if status == "no_audio_detected":
        log_pipeline_event("STT_WLK_NO_AUDIO", frames_sent=state.audio_frames_sent)

    return events


def _parse_server_text(raw: str, state: _WlkStreamState) -> list[dict[str, Any]]:
    raw = (raw or "").strip()
    if not raw:
        return []

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _parse_plain_text(raw)

    if isinstance(data, str):
        return _parse_plain_text(data)

    if not isinstance(data, dict):
        return []

    msg_type = str(data.get("type") or "").lower()
    if msg_type == "ready_to_stop":
        return _parse_wlk_message(data, state)

    if "lines" in data or "buffer_transcription" in data or data.get("status"):
        return _parse_wlk_message(data, state)

    if msg_type == "config":
        return []

    return _parse_generic_json(data)


def _parse_generic_json(data: dict[str, Any]) -> list[dict[str, Any]]:
    msg_type = str(
        data.get("type") or data.get("event") or data.get("message_type") or ""
    ).lower()
    nested = data.get("data")
    if isinstance(nested, dict):
        data = {**nested, **{k: v for k, v in data.items() if k != "data"}}

    text = (
        data.get("text")
        or data.get("transcript")
        or data.get("segment")
        or data.get("result")
        or ""
    )
    if isinstance(text, dict):
        text = text.get("text") or text.get("transcript") or ""
    text = str(text).strip()
    if not text:
        return []

    is_final = msg_type in (
        "final",
        "speech_final",
        "final_transcript",
        "transcript_final",
        "complete",
        "done",
    ) or bool(data.get("is_final") or data.get("final") or data.get("speech_final"))

    is_partial = not is_final
    return [{"text": text, "is_final": is_final, "is_partial": is_partial}]


def _parse_plain_text(raw: str) -> list[dict[str, Any]]:
    line = raw.strip()
    if not line:
        return []

    parts = line.split(maxsplit=2)
    if len(parts) == 3:
        try:
            float(parts[0])
            float(parts[1])
            text = parts[2].strip()
            if text:
                return [{"text": text, "is_final": True, "is_partial": False}]
        except ValueError:
            pass

    return [{"text": line, "is_final": False, "is_partial": True}]
