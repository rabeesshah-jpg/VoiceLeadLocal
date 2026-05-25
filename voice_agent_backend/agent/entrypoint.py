"""
LiveKit voice agent worker entrypoint.

Run: python -m agent dev
"""

from __future__ import annotations

from collections.abc import Callable

import asyncio
import json
import logging
import os
import sys
import uuid
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

from config.logging_setup import setup_worker_logging

setup_worker_logging(_BACKEND_ROOT)

import django

django.setup()

from livekit import rtc
from livekit.agents import JobContext, JobProcess, WorkerOptions, cli
from livekit.agents.inference.interruption import InterruptionDetectionError
from livekit.agents.voice import AgentSession
from livekit.agents.voice import room_io
from agent.pipeline.audio_input_options import resolve_audio_input_options
from livekit.agents.llm import ChatMessage
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
    UserInputTranscribedEvent,
)
from agent.pipeline.vad_silero import build_silero_vad

from agent.observability.latency import TtsCallTiming, TurnLatency
from agent.observability.pipeline_events import log_pipeline_event, tts_stream_mode_label
from agent.observability.pipeline_latency import TurnPipelineTracker
from agent.prompts import get_voice_agent_instructions, normalize_language
from agent.pipeline.llm_openrouter import build_openrouter_llm
from agent.pipeline.stt_deepgram import build_deepgram_stt, resolve_deepgram_stt
from agent.pipeline.stt_http_pool import ensure_stt_http_session
from agent.pipeline.stt_warmup import warmup_deepgram_stt
from agent.pipeline.user_stt_router import (
    default_user_stt_language,
    dominant_user_script,
    is_likely_mistranscribed_for_ar_call,
    resolve_user_stt_language_from_text,
)
from agent.pipeline.user_transcript_display import (
    needs_english_ui_translation,
    translate_to_english,
    user_message_for_ui,
    user_ui_english_enabled,
)
from agent.pipeline.stt_transcript_buffer import joined_transcript, merge_stt_segment
from agent.pipeline.tts_factory import build_tts, get_tts_provider
from agent.pipeline.voice_presets import resolve_tts_config
from agent.observability.structured_log import log_event
from agent.pipeline.data_channel import DataChannelPublisher
from agent.pipeline.session_events import log_call_event
from agent.greeting import get_call_greeting
from agent.voice_agent import VoiceAgent
from config.step_log import StepTimer, log_block

logger = logging.getLogger("agent.entrypoint")

FALLBACK_APOLOGY_EN = (
    "I'm having a little trouble right now. Could you say that again?"
)
FALLBACK_APOLOGY_AR = "عذراً، في مشكلة بسيطة. ممكن تعيد كلامك؟"


async def _wait_until_room_disconnected(room: rtc.Room) -> None:
    if room.connection_state == rtc.ConnectionState.CONN_DISCONNECTED:
        return
    done = asyncio.Event()

    def _on_connection_state_changed(state: rtc.ConnectionState) -> None:
        if state == rtc.ConnectionState.CONN_DISCONNECTED:
            done.set()

    room.on("connection_state_changed", _on_connection_state_changed)
    try:
        await done.wait()
    finally:
        room.off("connection_state_changed", _on_connection_state_changed)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _resolve_language(ctx: JobContext, db_session) -> str:
    if db_session and getattr(db_session, "language", None):
        return normalize_language(db_session.language)
    try:
        meta = json.loads(ctx.job.metadata or "{}")
    except json.JSONDecodeError:
        return normalize_language(None)
    return normalize_language(meta.get("language"))


def _resolve_tts_config(
    ctx: JobContext, db_session, *, language: str | None = None
) -> dict[str, str | float]:
    lang = language or _resolve_language(ctx, db_session)
    if db_session and db_session.persona_id:
        cfg = resolve_tts_config(db_session.persona_id, language=lang)
        if cfg:
            return cfg
    try:
        meta = json.loads(ctx.job.metadata or "{}")
    except json.JSONDecodeError:
        return resolve_tts_config(None, language=lang)
    tts = meta.get("tts")
    if isinstance(tts, dict) and tts:
        merged = dict(tts)
        merged.setdefault("lang", lang)
        return merged
    return resolve_tts_config(meta.get("persona_id"), language=lang)


def _build_session(
    tts_config: dict[str, str | float] | None = None,
    *,
    language: str = "en",
    on_tts_timing: Callable[[TtsCallTiming], None] | None = None,
    pipeline_tracker: TurnPipelineTracker | None = None,
    on_llm_first_token: Callable[[], None] | None = None,
    vad: object | None = None,
    stt_http_session: object | None = None,
) -> tuple[AgentSession, object]:
    operation = "BUILD_PIPELINE"
    with StepTimer(logger, operation, "load_vad"):
        if vad is None:
            vad = build_silero_vad()

    dg_model, dg_lang = resolve_deepgram_stt(language)
    with StepTimer(
        logger,
        operation,
        "init_deepgram_stt",
        model=dg_model,
        language=dg_lang,
        app_language=normalize_language(language),
        stt_language=dg_lang,
    ):
        stt = build_deepgram_stt(language, http_session=stt_http_session)

    aec_warmup = _env_float("VOICE_AGENT_AEC_WARMUP_S", 1.0)
    llm_model = _env("VOICE_AGENT_LLM_MODEL", "openai/gpt-4o-mini")
    with StepTimer(logger, operation, "init_openrouter_llm", model=llm_model):
        llm = build_openrouter_llm()

    tts_base = _env("TTS_BASE_URL") or _env("CHATTERBOX_TTS_URL", "http://127.0.0.1:7788")
    tts_cfg = tts_config or {}
    tts_voice = str(
        tts_cfg.get("speaker_wav")
        or tts_cfg.get("voice")
        or tts_cfg.get("predefined_voice_id")
        or _env("TTS_VOICE")
        or _env("VOICE_AGENT_CHATTERBOX_VOICE_ID", "female")
    )
    tts_lang = str(tts_cfg.get("lang") or _env("TTS_LANG", "en"))
    with StepTimer(
        logger,
        operation,
        "init_tts",
        provider=get_tts_provider(),
        base_url=tts_base,
        voice=tts_voice,
        lang=tts_lang,
    ):
        tts = build_tts(
            tts_cfg,
            on_timing=on_tts_timing,
            pipeline_tracker=pipeline_tracker,
            on_llm_first_token=on_llm_first_token,
        )

    with StepTimer(logger, operation, "assemble_agent_session"):
        # Use VAD interruptions — adaptive mode needs LiveKit inference WS and
        # often times out locally (408), which previously triggered a long apology TTS.
        session = AgentSession(
            vad=vad,
            stt=stt,
            llm=llm,
            tts=tts,
            aec_warmup_duration=aec_warmup,
            min_consecutive_speech_delay=0.0,
            turn_handling={
                "interruption": {
                    "enabled": True,
                    "mode": "vad",
                    "min_duration": 0.2,
                },
                "endpointing": {
                    "min_delay": _env_float("VOICE_AGENT_MIN_ENDPOINTING_DELAY", 0.20),
                    "max_delay": _env_float("VOICE_AGENT_MAX_ENDPOINTING_DELAY", 1.0),
                },
                "preemptive_generation": {
                    "enabled": _env_bool("VOICE_AGENT_PREEMPTIVE_GENERATION", True),
                    "preemptive_tts": _env_bool("VOICE_AGENT_PREEMPTIVE_TTS", True),
                    "max_retries": _env_int("VOICE_AGENT_PREEMPTIVE_MAX_RETRIES", 10),
                },
            },
            allow_interruptions=True,
            min_interruption_duration=0.2,
            preemptive_generation=_env_bool("VOICE_AGENT_PREEMPTIVE_GENERATION", True),
            min_endpointing_delay=_env_float("VOICE_AGENT_MIN_ENDPOINTING_DELAY", 0.20),
            max_endpointing_delay=_env_float("VOICE_AGENT_MAX_ENDPOINTING_DELAY", 1.0),
        )
        return session, stt


async def entrypoint(ctx: JobContext):
    job_id = ctx.job.id
    operation = "VOICE_JOB"

    with StepTimer(logger, operation, "livekit_connect", job_id=job_id):
        await ctx.connect()

    room = ctx.room
    room_name = room.name or "-"
    log_event("worker_joined", room=room_name, job_id=job_id)

    publisher = DataChannelPublisher(room)
    latency_holder: list[TurnLatency] = [TurnLatency()]
    pipeline_holder: list[TurnPipelineTracker] = [TurnPipelineTracker()]
    pipeline_holder[0].reset_turn(room=room_name)
    user_turn_seq: list[int] = [0]
    user_utterance_open: list[bool] = [False]
    stt_segment_parts: list[str] = []
    user_stt_lang_holder: list[str] = ["en-US"]
    greeting_complete: list[bool] = [False]
    greeting_text = ""

    def _on_tts_timing(timing: TtsCallTiming) -> None:
        latency_holder[0].record_tts_call(timing)

    def _on_llm_first_token() -> None:
        latency_holder[0].mark_llm_first_token()
        log_pipeline_event(
            "LLM_FIRST_TOKEN",
            room=room_name,
            turn_id=latency_holder[0].turn_id,
            source="turn_latency_callback",
        )

    def _publish_llm_start_milestone() -> None:
        ms = latency_holder[0].llm_start_ms()
        if ms is None:
            return

        async def _publish_milestone() -> None:
            await publisher.llm_start(
                turn_id=latency_holder[0].turn_id,
                llm_start_ms=ms,
            )

        try:
            asyncio.get_running_loop().create_task(_publish_milestone())
        except RuntimeError:
            pass

    db_session = None
    language = normalize_language(None)
    instructions = get_voice_agent_instructions(language)
    with StepTimer(logger, operation, "load_session_prompt", room=room_name):
        try:
            db_session = await _get_session_async(room_name)
            language = _resolve_language(ctx, db_session)
            instructions = get_voice_agent_instructions(language)
            if db_session and db_session.system_prompt.strip():
                instructions = db_session.system_prompt.strip()
            log_block(
                logger,
                logging.INFO,
                operation=operation,
                step="load_session_prompt",
                status="OK",
                room=room_name,
                language=language,
                prompt_source="database" if db_session else "metadata",
                prompt_len=len(instructions),
            )
        except Exception as exc:
            language = _resolve_language(ctx, None)
            instructions = get_voice_agent_instructions(language)
            logger.warning("Session prompt lookup skipped: %s", exc)

    user_stt_lang_holder[0] = default_user_stt_language(language)
    greeting_text = get_call_greeting(language)

    tts_config = _resolve_tts_config(ctx, db_session, language=language)
    log_block(
        logger,
        logging.INFO,
        operation=operation,
        step="tts_voice_config",
        status="OK",
        room=room_name,
        persona_id=getattr(db_session, "persona_id", "") if db_session else "",
        language=language,
        voice=tts_config.get("voice") or tts_config.get("predefined_voice_id"),
        lang=tts_config.get("lang"),
    )

    await log_call_event(room_name, "worker_joined", {"job_id": job_id})

    tts_base = _env("TTS_BASE_URL") or _env("CHATTERBOX_TTS_URL", "http://127.0.0.1:7788")
    tts_voice = str(
        tts_config.get("speaker_wav")
        or tts_config.get("voice")
        or tts_config.get("predefined_voice_id")
        or _env("TTS_VOICE")
        or _env("VOICE_AGENT_CHATTERBOX_VOICE_ID", "female")
    )
    tts_lang = str(tts_config.get("lang") or _env("TTS_LANG", "en"))

    from agent.pipeline.tts_http_pool import warmup_tts_connection

    prewarmed_vad = ctx.proc.userdata.get("vad")
    stt_http = await ensure_stt_http_session()
    agent_session, deepgram_stt = _build_session(
        tts_config,
        language=language,
        on_tts_timing=_on_tts_timing,
        pipeline_tracker=pipeline_holder[0],
        on_llm_first_token=_on_llm_first_token,
        vad=prewarmed_vad,
        stt_http_session=stt_http,
    )

    async def _warmup_stt() -> dict:
        with StepTimer(logger, operation, "deepgram_stt_warmup", room=room_name):
            result = await warmup_deepgram_stt(deepgram_stt)
            if not result.get("ok") and not result.get("skipped"):
                logger.warning("Deepgram STT warmup in job failed: %s", result)
            return result

    async def _warmup_tts() -> dict:
        with StepTimer(logger, operation, "tts_http_warmup", room=room_name):
            result = await warmup_tts_connection(
                tts_base,
                voice=tts_voice,
                lang=tts_lang,
                force=True,
                prefetch_text=greeting_text,
            )
            if not result.get("ok"):
                logger.warning("TTS HTTP warmup in job failed: %s", result)
            return result

    with StepTimer(logger, operation, "wait_for_caller", room=room_name):
        participant, _warmup_tts_res, _warmup_stt_res = await asyncio.gather(
            ctx.wait_for_participant(),
            _warmup_tts(),
            _warmup_stt(),
        )
    log_block(
        logger,
        logging.INFO,
        operation=operation,
        step="caller_joined",
        status="OK",
        room=room_name,
        participant_identity=participant.identity,
    )
    agent = VoiceAgent(
        instructions=instructions,
        language=language,
        allow_interruptions=True,
        publisher=publisher,
        pipeline_tracker=pipeline_holder[0],
        on_llm_first_token=_on_llm_first_token,
        greeting_complete=greeting_complete,
        room=room_name,
    )
    stt_sample_rate = int(_env("VOICE_AGENT_STT_SAMPLE_RATE", "16000") or "16000")
    room_options = room_io.RoomOptions(
        participant_identity=participant.identity,
        audio_input=resolve_audio_input_options(sample_rate=stt_sample_rate),
    )

    def _route_user_stt_language(display_text: str) -> None:
        if normalize_language(language) != "ar":
            return
        next_lang = resolve_user_stt_language_from_text(
            display_text,
            conversation_language=language,
            current_language=user_stt_lang_holder[0],
        )
        if next_lang == user_stt_lang_holder[0]:
            return
        prev_lang = user_stt_lang_holder[0]
        user_stt_lang_holder[0] = next_lang
        deepgram_stt.update_options(language=next_lang)
        if prev_lang == "en-US" and next_lang == "ar-SA" and stt_segment_parts:
            if dominant_user_script("".join(stt_segment_parts)) != "arabic":
                stt_segment_parts.clear()
        if prev_lang == "ar-SA" and next_lang == "en-US" and stt_segment_parts:
            if dominant_user_script("".join(stt_segment_parts)) != "latin":
                stt_segment_parts.clear()
        log_block(
            logger,
            logging.INFO,
            operation="STT",
            step="user_stt_language_switch",
            status="OK",
            room=room_name,
            language=next_lang,
            text_preview=display_text[:80],
        )

    @agent_session.on("user_input_transcribed")
    def on_transcript(ev: UserInputTranscribedEvent):
        import asyncio

        if ev.is_final:
            latency_holder[0].mark_stt_final()
            if ev.transcript.strip():
                stt_segment_parts[:] = merge_stt_segment(
                    stt_segment_parts, ev.transcript
                )

        async def _handle():
            if not greeting_complete[0]:
                return

            component = "STT"
            display_text = joined_transcript(
                stt_segment_parts,
                interim="" if ev.is_final else ev.transcript,
            )
            if ev.is_final:
                pipeline_holder[0].reset_turn(room=room_name)
                pipeline_holder[0].mark_stt_final(transcript_preview=display_text)
                log_pipeline_event(
                    "USER_FINAL_TRANSCRIPT",
                    room=room_name,
                    turn_id=pipeline_holder[0].turn_id,
                    text_preview=display_text[:80],
                    tts_mode=tts_stream_mode_label(),
                )
                log_block(
                    logger,
                    logging.INFO,
                    operation=component,
                    step="final_transcript",
                    status="OK",
                    room=room_name,
                    text=display_text[:200],
                    is_final=True,
                )
            else:
                log_pipeline_event(
                    "STT_PARTIAL_TRANSCRIPT",
                    room=room_name,
                    turn_id=pipeline_holder[0].turn_id,
                    text_preview=display_text[:80],
                )
                log_block(
                    logger,
                    logging.INFO,
                    operation=component,
                    step="partial_transcript",
                    status="OK",
                    room=room_name,
                    text=display_text[:120],
                    is_final=False,
                )
            if display_text.strip():
                if (
                    normalize_language(language) == "ar"
                    and is_likely_mistranscribed_for_ar_call(display_text)
                ):
                    stt_segment_parts.clear()
                    if user_stt_lang_holder[0] != "ar-SA":
                        user_stt_lang_holder[0] = "ar-SA"
                        deepgram_stt.update_options(language="ar-SA")
                    log_block(
                        logger,
                        logging.WARNING,
                        operation="STT",
                        step="skip_mistranscribed_user_text",
                        status="SKIP",
                        room=room_name,
                        text_preview=display_text[:80],
                    )
                    return

                _route_user_stt_language(display_text)
                if (
                    not ev.is_final
                    and normalize_language(language) == "ar"
                    and user_stt_lang_holder[0] == "en-US"
                    and dominant_user_script(display_text) == "latin"
                    and stt_segment_parts
                ):
                    # ar-SA can emit Arabic script for English; prefer fresh en-US segments.
                    prev = dominant_user_script(joined_transcript(stt_segment_parts, interim=""))
                    if prev not in ("latin", "unknown"):
                        stt_segment_parts.clear()

                if ev.is_final:
                    if not user_utterance_open[0]:
                        user_turn_seq[0] += 1
                        user_utterance_open[0] = True
                    turn_seq = user_turn_seq[0]

                    # Publish final user line before translation so the UI turn exists
                    # before llm_response events (translation can lag agent reply).
                    prime_ui = (
                        "…"
                        if user_ui_english_enabled()
                        and needs_english_ui_translation(display_text)
                        else display_text.strip()
                    )
                    await publisher.user_transcript(
                        prime_ui,
                        is_final=True,
                        message_id=str(uuid.uuid4()),
                        turn_seq=turn_seq,
                    )

                    ui_text = display_text.strip()
                    if user_ui_english_enabled() and needs_english_ui_translation(
                        display_text
                    ):
                        ui_text = await translate_to_english(display_text)
                    if ui_text.strip() and ui_text.strip() != prime_ui.strip():
                        await publisher.user_transcript(
                            ui_text,
                            is_final=True,
                            message_id=str(uuid.uuid4()),
                            turn_seq=turn_seq,
                        )

                    await log_call_event(
                        room_name,
                        "user_transcript",
                        {"text": ui_text[:500], "stt_raw": display_text[:500]},
                    )
                    return

                if not user_utterance_open[0]:
                    user_turn_seq[0] += 1
                    user_utterance_open[0] = True

                ui_text = await user_message_for_ui(
                    display_text,
                    is_final=False,
                )
                if ui_text is None:
                    return

                await publisher.user_transcript(
                    ui_text,
                    is_final=False,
                    turn_seq=user_turn_seq[0],
                )

        asyncio.create_task(_handle())

    @agent_session.on("agent_state_changed")
    def on_agent_state(ev: AgentStateChangedEvent):
        import asyncio

        async def _handle():
            state = ev.new_state
            old = ev.old_state
            log_block(
                logger,
                logging.INFO,
                operation="AGENT_STATE",
                step="transition",
                status="OK",
                room=room_name,
                from_state=old,
                to_state=state,
            )
            if state == "interrupted" or (state == "thinking" and old == "speaking"):
                await publisher.llm_playback_end(interrupted=True)
            if state == "thinking":
                stt_segment_parts.clear()
                user_utterance_open[0] = False
                pipeline_holder[0].mark_llm_dispatch()
                latency_holder[0].mark_llm_dispatch()
                log_pipeline_event(
                    "LLM_START",
                    room=room_name,
                    turn_id=latency_holder[0].turn_id,
                    pipeline_turn_id=pipeline_holder[0].turn_id,
                    tts_mode=tts_stream_mode_label(),
                )
                _publish_llm_start_milestone()
            if state == "speaking":
                latency_holder[0].mark_audio_out()
            if state == "listening" and old == "speaking":
                await publisher.llm_playback_end()
                pipeline_holder[0].log_turn_once()
                payload = latency_holder[0].to_payload()
                log_block(
                    logger,
                    logging.INFO,
                    operation="TURN_LATENCY",
                    step="turn_complete",
                    status="OK",
                    room=room_name,
                    **{k: v for k, v in payload.items() if v is not None},
                )
                await publisher.latency(payload)
                await log_call_event(room_name, "latency", payload)
                latency_holder[0] = TurnLatency()
                pipeline_holder[0].reset_turn(room=room_name)
            if state == "listening" and not greeting_complete[0]:
                return

            await publisher.agent_state(state)
            await log_call_event(room_name, "agent_state", {"state": state, "old_state": old})

        asyncio.create_task(_handle())

    @agent_session.on("conversation_item_added")
    def on_conversation_item(ev: ConversationItemAddedEvent):
        import asyncio

        async def _handle():
            item = ev.item
            if not isinstance(item, ChatMessage) or item.role != "assistant":
                return
            text = (item.text_content or "").strip()
            if text and text == greeting_text:
                return
            if text:
                log_block(
                    logger,
                    logging.INFO,
                    operation="LLM",
                    step="assistant_message",
                    status="OK",
                    room=room_name,
                    text=text[:200],
                )
                await publisher.llm_response(text, is_final=True)

        asyncio.create_task(_handle())

    @agent_session.on("error")
    def on_error(ev: ErrorEvent):
        import asyncio

        async def _handle():
            err = ev.error
            if isinstance(err, InterruptionDetectionError):
                log_block(
                    logger,
                    logging.WARNING,
                    operation="PIPELINE",
                    step="interruption_detection",
                    status="SKIP",
                    room=room_name,
                    error=str(err)[:400],
                    note="Using VAD interruption; no fallback apology",
                )
                return

            code = "pipeline_error"
            err_type = str(type(err)).lower()
            if "stt" in err_type:
                code = "stt_failed"
            elif "tts" in err_type:
                code = "tts_failed"
            elif "llm" in err_type:
                code = "llm_failed"
            log_block(
                logger,
                logging.ERROR,
                operation="PIPELINE",
                step=code,
                status="FAIL",
                room=room_name,
                error=str(err)[:400],
            )
            await publisher.error(code, str(err))
            await log_call_event(room_name, "error", {"code": code, "message": str(err)[:300]})
            try:
                with StepTimer(logger, operation, "fallback_tts_apology", room=room_name):
                    apology = (
                        FALLBACK_APOLOGY_AR
                        if language == "ar"
                        else FALLBACK_APOLOGY_EN
                    )
                    await agent_session.say(apology, allow_interruptions=True)
            except Exception as say_exc:
                logger.warning("Fallback say failed: %s", say_exc)

        asyncio.create_task(_handle())

    try:
        with StepTimer(logger, operation, "agent_session_run", room=room_name, job_id=job_id):
            await agent_session.start(agent=agent, room=room, room_options=room_options)
            if not greeting_complete[0]:
                await publisher.agent_state("listening")
                greeting_complete[0] = True
        with StepTimer(logger, operation, "agent_session_active", room=room_name, job_id=job_id):
            await _wait_until_room_disconnected(room)
        await log_call_event(room_name, "worker_session_ended", {})
        log_block(
            logger,
            logging.INFO,
            operation=operation,
            step="job_complete",
            status="OK",
            room=room_name,
            job_id=job_id,
        )
    except Exception as exc:
        logger.exception("Agent session failed")
        await log_call_event(room_name, "worker_failed", {"error": str(exc)[:300]})
        await publisher.error("worker_failed", str(exc))
        raise


async def _get_session_async(room_name: str):
    from asgiref.sync import sync_to_async
    from apps.calls.models import CallSession

    with StepTimer(logger, "DATABASE", "fetch_call_session", room=room_name):
        return await sync_to_async(
            lambda: CallSession.objects.filter(room_name=room_name).first(),
            thread_sensitive=True,
        )()


def _startup_provider_checks():
    from agent.providers.supertonic_health import check_supertonic_on_startup

    log_block(
        logger,
        logging.INFO,
        operation="WORKER",
        step="env_keys_present",
        status="CHECK",
        deepgram=bool(_env("DEEPGRAM_API_KEY")),
        openrouter=bool(_env("OPENROUTER_API_KEY")),
        tts_base_url=_env("TTS_BASE_URL") or _env("CHATTERBOX_TTS_URL") or "default",
        tts_provider=get_tts_provider(),
        tts_voice=_env("TTS_VOICE", "female"),
        tts_lang=_env("TTS_LANG", "en"),
        openrouter_model=_env("VOICE_AGENT_LLM_MODEL", "openai/gpt-4o-mini"),
        livekit_url=_env("LIVEKIT_URL"),
    )
    check_supertonic_on_startup()


def _worker_prewarm(proc: JobProcess) -> None:
    """Load Silero VAD once per worker process (avoids per-call model load)."""
    proc.userdata["vad"] = build_silero_vad()


def main():
    from config.logging_setup import PIPELINE_LATENCY_LOG_NAME

    log_path = setup_worker_logging(_BACKEND_ROOT)
    log_block(
        logger,
        logging.INFO,
        operation="WORKER",
        step="startup",
        status="OK",
        log_file=str(log_path),
        pipeline_latency_log=str(_BACKEND_ROOT / "logs" / PIPELINE_LATENCY_LOG_NAME),
        agent_name=_env("VOICE_AGENT_NAME", "voice-agent"),
    )
    _startup_provider_checks()
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=_env("VOICE_AGENT_NAME", "voice-agent"),
            prewarm_fnc=_worker_prewarm,
        )
    )


if __name__ == "__main__":
    main()
