"""LiveKit Agent with LLM transcript streaming to the room data channel."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterable, Callable

from asgiref.sync import sync_to_async
from livekit import rtc
from livekit.agents import function_tool, stt
from livekit.agents.types import TimedString
from livekit.agents.voice import Agent
from livekit.agents.voice.agent import ModelSettings

from agent.greeting import get_call_greeting
from agent.observability.pipeline_events import log_pipeline_event
from agent.observability.pipeline_latency import TurnPipelineTracker
from agent.pipeline.data_channel import DataChannelPublisher
from agent.pipeline.guardrails import is_guardrail_trigger, refusal_text
from agent.pipeline.partial_stt_preemptive import (
    PartialSttPreemptivePolicy,
    preflight_from_interim,
)
from agent.pipeline.stt_config import is_faster_whisper_provider

logger = logging.getLogger("agent.voice_agent")


def _text_chunk(delta: str | TimedString) -> str:
    return str(delta)


def _extract_last_user_text(chat_ctx) -> str:
    """Best-effort extraction of the caller's most recent message text from
    a LiveKit ChatContext, for the guardrail pattern check in llm_node
    below. Defensive against minor API-shape differences across SDK
    versions — returns "" (never raises) if the expected attributes aren't
    present, so guardrail layer 2 just falls through to a normal LLM call
    rather than breaking the turn.
    """
    items = None
    for attr in ("items", "messages"):
        items = getattr(chat_ctx, attr, None)
        if items:
            break
    if not items:
        return ""
    for item in reversed(list(items)):
        role = getattr(item, "role", None)
        if role == "user":
            text = getattr(item, "text_content", None)
            if text is None:
                content = getattr(item, "content", None)
                if isinstance(content, list):
                    text = " ".join(str(c) for c in content if isinstance(c, str))
                elif isinstance(content, str):
                    text = content
            return (text or "").strip()
    return ""


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
        on_end_call: Callable[[], None] | None = None,
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
        self._on_end_call = on_end_call
        self._greeting_complete = greeting_complete
        self._room = room
        # Tracks in-flight fire-and-forget save_lead_info database writes.
        # Holding a strong reference here prevents asyncio from garbage
        # collecting the task before it completes (a well-known asyncio
        # gotcha with create_task() when nothing else references the
        # task) — not used to block or await anything; the post-call
        # transcript extraction backstop is the real safety net if a call
        # ends before a write finishes.
        self._pending_save_tasks: set[asyncio.Task] = set()
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

    @function_tool()
    async def save_lead_info(
        self,
        name: str | None = None,
        company: str | None = None,
        whatsapp_number: str | None = None,
        city: str | None = None,
        need: str | None = None,
        has_existing_website: bool | None = None,
        website_action: str | None = None,
        business_description: str | None = None,
        start_timeline: str | None = None,
        lead_intent: str | None = None,
        appointment_time: str | None = None,
    ) -> str:
        """Save the lead's details. Called up to twice per conversation: an
        early checkpoint with name/company/city, and a final save with
        everything else. Always pass every parameter listed in this
        function's signature on every call. Use null for any field you do
        not have yet — never omit a parameter entirely, omitting a
        parameter will cause the call to fail schema validation.
        """
        logger.info(
            "save_lead_info CALLED room=%s name=%s company=%s city=%s",
            self._room, name, company, city,
        )
        # Fire-and-forget: the database write no longer blocks the LLM's
        # tool-calling round-trip. The write still happens reliably in the
        # background (tracked in self._pending_save_tasks so it can't be
        # garbage-collected mid-write) — and even in the rare case a task
        # doesn't finish before the call ends, the post-call transcript
        # extraction backstop (agent.pipeline.lead_extraction, wired in
        # entrypoint.py's _extract_and_fill_lead_async) independently
        # reconstructs and fills in lead fields from the full conversation
        # after disconnect, so no data is lost either way.
        task = asyncio.create_task(
            self._save_lead_fields_background(
                name=name,
                company=company,
                whatsapp_number=whatsapp_number,
                city=city,
                need=need,
                has_existing_website=has_existing_website,
                website_action=website_action,
                business_description=business_description,
                start_timeline=start_timeline,
                lead_intent=lead_intent,
                appointment_time=appointment_time,
            )
        )
        self._pending_save_tasks.add(task)
        task.add_done_callback(self._pending_save_tasks.discard)
        return "Lead info saved."

    async def _save_lead_fields_background(self, **fields) -> None:
        try:
            await self._save_lead_fields(**fields)
        except Exception:
            logger.exception(
                "save_lead_info background save FAILED room=%s", self._room
            )

    @sync_to_async
    def _save_lead_fields(self, **fields) -> None:
        from apps.calls.models import CallSession, Lead

        session = CallSession.objects.filter(room_name=self._room).first()
        if session is None:
            logger.warning("save_lead_info: no CallSession for room=%s", self._room)
            return
        lead, _ = Lead.objects.get_or_create(session=session)
        changed = []
        for field, value in fields.items():
            if value is not None and value != "":
                setattr(lead, field, value)
                changed.append(field)
        if changed:
            lead.save(update_fields=[*changed, "updated_at"])
            logger.info(
                "save_lead_info: saved fields=%s room=%s lead_id=%s",
                changed, self._room, lead.pk,
            )
        else:
            logger.info("save_lead_info: called with no changed fields, room=%s", self._room)

    @function_tool()
    async def end_call(self) -> str:
        """Call this once you are ready to end the call — once you have
        either captured what you need, or the caller has made clear
        they're done / not interested.

        Do NOT say your own goodbye or closing line first — calling this
        tool automatically speaks a closing message (mentioning next steps
        and saying goodbye) and ends the call for you. Just call it
        directly once you're ready to wrap up; you do not need to say
        anything else before or after calling it.
        """
        logger.info("end_call CALLED room=%s", self._room)
        closing_text = (
            "شكراً جزيلاً على وقتك! راقب رسائلك للخطوات القادمة. يوم سعيد! مع السلامة!"
            if self._language == "ar"
            else "Thanks so much for your time! Keep an eye on your messages for "
            "next steps. Have a great day! Goodbye!"
        )
        publisher = self._publisher
        try:
            if publisher:
                await publisher.agent_state("speaking")
                await publisher.llm_response(closing_text, is_final=False)
            handle = self.session.say(
                closing_text,
                allow_interruptions=True,
                add_to_chat_ctx=True,
            )
            await handle.wait_for_playout()
            if publisher:
                await publisher.llm_response(closing_text, is_final=True)
                await publisher.llm_playback_end(interrupted=handle.interrupted)
        except Exception:
            logger.exception(
                "end_call: closing message failed to play, room=%s", self._room
            )
        if self._on_end_call:
            self._on_end_call()
        return "Goodbye message already delivered; call will now end."

    def llm_node(self, chat_ctx, tools, model_settings: ModelSettings):
        return self._llm_node_with_guardrails(chat_ctx, tools, model_settings)

    async def _llm_node_with_guardrails(self, chat_ctx, tools, model_settings: ModelSettings):
        """Guardrail layer 2: if the caller's latest message matches a known
        jailbreak/injection pattern, skip the real LLM call entirely and
        yield a fixed refusal instead — cheaper and faster than a normal
        turn, and independent of whether the model would have complied.
        Falls through to the normal LLM node for everything else, and on
        any extraction issue, so this never breaks a call.
        """
        try:
            last_user_text = _extract_last_user_text(chat_ctx)
        except Exception:
            last_user_text = ""

        if last_user_text and is_guardrail_trigger(last_user_text):
            logger.info(
                "guardrail_layer2_triggered room=%s text=%r",
                self._room, last_user_text[:160],
            )
            yield refusal_text(self._language)
            return

        async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
            yield chunk

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
                    # Fire-and-forget: awaiting this inline was blocking the
                    # LLM's token generation loop on a network round-trip for
                    # every single token (previously reliable=True on every
                    # call), which was the primary cause of abnormally slow
                    # per-token generation seen in pipeline latency logs
                    # (~300ms/token). The UI only needs the latest partial
                    # text eventually, not a guaranteed-ordered ack per token.
                    asyncio.create_task(
                        self._publisher.llm_response("".join(parts), is_final=False)
                    )
            yield delta