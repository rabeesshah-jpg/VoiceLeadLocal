"""LiveKit Agent with LLM transcript streaming to the room data channel."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterable, Callable

from livekit import rtc
from livekit.agents import stt
from livekit.agents.types import TimedString
from livekit.agents.voice import Agent
from livekit.agents.voice.agent import ModelSettings

from agent.greeting import get_call_greeting
from agent.observability.pipeline_events import log_pipeline_event
from agent.observability.pipeline_latency import TurnPipelineTracker
from agent.pipeline.data_channel import DataChannelPublisher
from agent.pipeline.partial_stt_preemptive import (
    PartialSttPreemptivePolicy,
    preflight_from_interim,
)
from agent.pipeline.stt_config import is_faster_whisper_provider

logger = logging.getLogger("agent.voice_agent")


def _text_chunk(delta: str | TimedString) -> str:
    return str(delta)


class VoiceAgent(Agent):
    """Streams LLM/transcription text to the frontend while audio is synthesized."""

    def __init__(
        self,
        *,
        language: str = "en",
        publisher: DataChannelPublisher | None = None,
        pipeline_tracker: TurnPipelineTracker | None = None,
        on_llm_first_token: Callable[[], None] | None = None,
        on_vad_speech_start: Callable[[], None] | None = None,
        on_vad_speech_end: Callable[[], None] | None = None,
        greeting_complete: list[bool] | None = None,
        room: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._language = language
        self._publisher = publisher
        self._pipeline = pipeline_tracker
        self._on_llm_first_token = on_llm_first_token
        self._on_vad_speech_start = on_vad_speech_start
        self._on_vad_speech_end = on_vad_speech_end
        self._greeting_complete = greeting_complete
        self._room = room
        policy = PartialSttPreemptivePolicy.from_env()
        if is_faster_whisper_provider():
            policy.enabled = False
        self._partial_preemptive = policy

    async def on_enter(self) -> None:
        greeting = get_call_greeting(self._language)
        publisher = self._publisher
        try:
            if publisher:
                await publisher.agent_state("speaking")
                await publisher.llm_response(greeting, is_final=False)
            handle = self.session.say(
                greeting,
                allow_interruptions=True,
                add_to_chat_ctx=True,
            )
            await handle.wait_for_playout()
            if publisher:
                await publisher.llm_response(greeting, is_final=True)
                await publisher.llm_playback_end(interrupted=handle.interrupted)
                await publisher.agent_state("listening")
        except Exception:
            logger.exception("Call greeting failed")
            if publisher:
                await publisher.agent_state("listening")
        finally:
            if self._pipeline:
                self._pipeline.end_greeting_phase()
            if self._greeting_complete is not None:
                self._greeting_complete[0] = True

    def stt_node(
        self, audio: AsyncIterable[rtc.AudioFrame], model_settings: ModelSettings
    ):
        return self._stt_node_partial_preemptive(audio, model_settings)

    async def _stt_node_partial_preemptive(
        self, audio: AsyncIterable[rtc.AudioFrame], model_settings: ModelSettings
    ):
        """Emit PREFLIGHT from stable interim text so LiveKit can start LLM early."""
        async for event in Agent.default.stt_node(self, audio, model_settings):
            if event.type == stt.SpeechEventType.START_OF_SPEECH:
                self._partial_preemptive.reset()
                if self._greeting_complete is None or self._greeting_complete[0]:
                    if self._on_vad_speech_start:
                        self._on_vad_speech_start()
                    if self._pipeline:
                        self._pipeline.mark_user_speech_start()
            elif event.type == stt.SpeechEventType.END_OF_SPEECH:
                self._partial_preemptive.reset()
                if self._greeting_complete is None or self._greeting_complete[0]:
                    if self._pipeline:
                        self._pipeline.mark_user_speech_end()
                    if self._on_vad_speech_end:
                        self._on_vad_speech_end()
            elif (
                event.type == stt.SpeechEventType.INTERIM_TRANSCRIPT
                and event.alternatives
            ):
                text = event.alternatives[0].text
                now = time.perf_counter()
                if self._partial_preemptive.should_emit_preflight(text, now=now):
                    self._partial_preemptive.mark_emitted(text, now=now)
                    log_pipeline_event(
                        "STT_PARTIAL_PREFLIGHT",
                        room=self._room,
                        turn_id=self._pipeline.turn_id if self._pipeline else "",
                        text_preview=text.strip()[:80],
                        chars=len(text.strip()),
                    )
                    logger.info(
                        "partial_stt_preflight text=%r chars=%s",
                        text.strip()[:120],
                        len(text.strip()),
                    )
                    yield preflight_from_interim(event)
            yield event

    def transcription_node(
        self, text: AsyncIterable[str | TimedString], model_settings: ModelSettings
    ):
        return self._transcription_node_stream(text, model_settings)

    async def _transcription_node_stream(
        self, text: AsyncIterable[str | TimedString], model_settings: ModelSettings
    ):
        parts: list[str] = []
        async for delta in Agent.default.transcription_node(self, text, model_settings):
            chunk = _text_chunk(delta)
            if chunk:
                parts.append(chunk)
                if self._pipeline:
                    self._pipeline.mark_llm_first_token(token_preview=chunk)
                if self._on_llm_first_token:
                    self._on_llm_first_token()
                if self._publisher:
                    await self._publisher.llm_response("".join(parts), is_final=False)
            yield delta

        # Do not send is_final here — transcription ends before TTS playout finishes.
        # Final text is published after playback (conversation_item_added / llm_playback_end).
