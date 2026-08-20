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
import re
import sys
import uuid
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

from agent.worker_env import enforce_worker_runtime_policy, resolve_openrouter_model

enforce_worker_runtime_policy()

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
from agent.observability.streaming_audit import log_streaming_audit
from agent.pipeline.user_turn_streaming import (
    TurnCommitController,
    barge_in_enabled,
)
from agent.prompts import get_voice_agent_instructions, normalize_language
from agent.pipeline.llm_openrouter import build_openrouter_llm
from agent.pipeline.stt_config import is_deepgram_provider, is_faster_whisper_provider
from agent.pipeline.stt_transcript_utils import (
    clean_transcript,
    transcript_extends_prior,
    validate_final_transcript,
)
from agent.pipeline.stt_factory import build_stt, get_stt_provider, resolve_stt_log_fields
from agent.pipeline.stt_http_pool import ensure_stt_http_session
from agent.pipeline.stt_warmup import warmup_stt
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

# Deterministic backstop for ending calls: rather than relying solely on the
# LLM reliably calling the end_call tool (which has proven unreliable in
# testing), also check the assistant's own generated text for a closing
# phrase. If Nora's own words sound like a genuine goodbye, the call ends
# after that message finishes playing — independent of whether end_call was
# also invoked. This mirrors the guardrails.py approach: critical behavior
# should not depend purely on the model choosing to comply.
import re as _re

_GOODBYE_PATTERNS: tuple[_re.Pattern, ...] = tuple(
    _re.compile(p, _re.IGNORECASE)
    for p in (
        r"\bgoodbye\b",
        r"\bgood bye\b",
        r"\btake care\b",
        r"\bhave a (great|good|nice|wonderful) day\b",
        r"\bمع السلامة\b",
        r"\bيوم سعيد\b",
    )
)


def _looks_like_goodbye(text: str) -> bool:
    if not text:
        return False
    return any(p.search(text) for p in _GOODBYE_PATTERNS)


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


def _extract_caller_number(identity: str) -> str | None:
    """Extract the E.164 phone number from a SIP participant identity like
    'sip_+923121363468'. Returns None for non-SIP identities (e.g. web
    callers, whose identity looks like 'user-<uuid>' and has no phone
    number to offer).
    """
    if not identity:
        return None
    match = re.match(r"^sip_(\+?\d{6,15})$", identity)
    if not match:
        return None
    number = match.group(1)
    if not number.startswith("+"):
        number = f"+{number}"
    return number


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
    from apps.calls.services.voice_tts_config import merge_tts_config_from_sources

    lang = language or _resolve_language(ctx, db_session)
    try:
        meta = json.loads(ctx.job.metadata or "{}")
    except json.JSONDecodeError:
        meta = {}

    persona_id = ""
    if db_session and db_session.persona_id:
        persona_id = db_session.persona_id
    elif meta.get("persona_id"):
        persona_id = str(meta["persona_id"])

    return merge_tts_config_from_sources(
        language=lang,
        persona_id=persona_id,
        meta=meta,
        db_session=db_session,
    )


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

    stt_fields = resolve_stt_log_fields(language)
    with StepTimer(
        logger,
        operation,
        "init_stt",
        app_language=normalize_language(language),
        **stt_fields,
    ):
        stt = build_stt(language, http_session=stt_http_session)

    aec_warmup = _env_float("VOICE_AGENT_AEC_WARMUP_S", 1.0)
    from agent.worker_env import resolve_openrouter_model, resolve_tts_base_url

    llm_model = resolve_openrouter_model()
    with StepTimer(logger, operation, "init_openrouter_llm", model=llm_model):
        llm = build_openrouter_llm()

    from apps.calls.services.voice_tts_config import effective_tts_voice

    tts_base = resolve_tts_base_url()
    tts_cfg = tts_config or {}
    tts_voice = effective_tts_voice(tts_cfg)
    tts_lang = str(tts_cfg.get("lang") or _env("TTS_LANG", "en"))
    with StepTimer(
        logger,
        operation,
        "init_tts",
        provider=get_tts_provider(),
        base_url=tts_base,
        voice_mode=tts_cfg.get("voice_mode", "preset"),
        voice=tts_voice,
        provider_voice_id=tts_cfg.get("provider_voice_id", ""),
        lang=tts_lang,
    ):
        tts = build_tts(
            tts_cfg,
            on_timing=on_tts_timing,
            pipeline_tracker=pipeline_tracker,
            on_llm_first_token=on_llm_first_token,
        )

    with StepTimer(logger, operation, "assemble_agent_session"):
        # Faster-whisper finals are debounced in entrypoint; preemptive LLM causes
        # duplicate/early requests before STT is stable.
        preemptive_llm = _env_bool("VOICE_AGENT_PREEMPTIVE_GENERATION", True)
        if is_faster_whisper_provider():
            preemptive_llm = _env_bool("VOICE_AGENT_PREEMPTIVE_GENERATION", False)
        preemptive_tts = _env_bool("VOICE_AGENT_PREEMPTIVE_TTS", preemptive_llm)
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
                    "enabled": preemptive_llm,
                    "preemptive_tts": preemptive_tts,
                    "max_retries": _env_int("VOICE_AGENT_PREEMPTIVE_MAX_RETRIES", 10),
                },
            },
            allow_interruptions=True,
            min_interruption_duration=0.2,
            preemptive_generation=preemptive_llm,
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
    pipeline_holder: list[TurnPipelineTracker] = [TurnPipelineTracker(room=room_name)]
    user_turn_seq: list[int] = [0]
    user_utterance_open: list[bool] = [False]
    vad_speech_active: list[bool] = [False]
    user_final_committed_turns: set[int] = set()
    turn_commit_holder: list[TurnCommitController] = [TurnCommitController()]
    llm_dispatch_logged: set[str] = set()
    last_llm_turn_seq: list[int] = [0]
    stt_segment_parts: list[str] = []
    user_stt_lang_holder: list[str] = ["en-US"]
    greeting_complete: list[bool] = [False]
    call_should_end: list[bool] = [False]
    silence_watchdog_task: list[asyncio.Task | None] = [None]
    SILENCE_WARNING_S = _env_float("VOICE_AGENT_SILENCE_WARNING_S", 20.0)
    SILENCE_DISCONNECT_S = _env_float("VOICE_AGENT_SILENCE_DISCONNECT_S", 15.0)
    transcript_lines: list[str] = []
    greeting_text = ""

    def _on_tts_timing(timing: TtsCallTiming) -> None:
        latency_holder[0].record_tts_call(timing)

    def _on_llm_first_token() -> None:
        latency_holder[0].mark_llm_first_token()
        pipeline_holder[0].mark_llm_first_token()

    def _on_llm_metrics_collected(metrics: object) -> None:
        from livekit.agents.metrics import LLMMetrics

        if isinstance(metrics, LLMMetrics):
            pipeline_holder[0].record_llm_usage(
                prompt_tokens=metrics.prompt_tokens,
                completion_tokens=metrics.completion_tokens,
                total_tokens=metrics.total_tokens,
            )

    def _publish_llm_start_milestone() -> None:
        ms = latency_holder[0].llm_start_ms()
        if ms is None:
            return

        async def _publish_milestone() -> None:
            await publisher.llm_start(
                turn_id=pipeline_holder[0].turn_id,
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

    # No matching CallSession found in DB (e.g. this job came from a SIP/phone
    # call rather than the browser widget, which is the only flow that creates
    # a CallSession up front via /api/calls/start/). Auto-create one now so
    # that every log_call_event(...) call below actually persists instead of
    # silently no-op'ing (session_events._persist_call_event returns early
    # when it can't find a matching CallSession for room_name).
    if db_session is None:
        try:
            db_session = await _create_session_async(
                room_name, language=language, system_prompt=instructions
            )
            log_block(
                logger,
                logging.INFO,
                operation="DATABASE",
                step="call_session_auto_created",
                status="OK",
                room=room_name,
                reason="no_existing_session_found_likely_sip_call",
            )
        except Exception as exc:
            logger.warning("Could not auto-create CallSession for %s: %s", room_name, exc)

    user_stt_lang_holder[0] = default_user_stt_language(language)
    greeting_text = get_call_greeting(language)

    try:
        job_meta = json.loads(ctx.job.metadata or "{}")
    except json.JSONDecodeError:
        job_meta = {}
    log_block(
        logger,
        logging.INFO,
        operation=operation,
        step="worker_metadata_received",
        status="OK",
        room=room_name,
        job_id=job_id,
        voice_mode=job_meta.get("voice_mode", "preset"),
        voice_profile_id=job_meta.get("voice_profile_id", ""),
        provider_voice_id=job_meta.get("provider_voice_id", ""),
        fallback_voice=job_meta.get("fallback_voice", ""),
    )

    tts_config = _resolve_tts_config(ctx, db_session, language=language)
    from apps.calls.services.voice_tts_config import effective_tts_voice

    effective_voice = effective_tts_voice(tts_config)
    log_block(
        logger,
        logging.INFO,
        operation=operation,
        step="tts_voice_config",
        status="OK",
        room=room_name,
        persona_id=getattr(db_session, "persona_id", "") if db_session else "",
        language=language,
        voice_mode=tts_config.get("voice_mode", "preset"),
        preset_voice=tts_config.get("voice") or tts_config.get("predefined_voice_id"),
        effective_tts_voice=effective_voice,
        lang=tts_config.get("lang"),
        voice_profile_id=tts_config.get("voice_profile_id", ""),
        provider_voice_id=tts_config.get("provider_voice_id", ""),
        fallback_voice=tts_config.get("fallback_voice", ""),
    )

    await log_call_event(room_name, "worker_joined", {"job_id": job_id})

    from agent.worker_env import resolve_openrouter_model, resolve_tts_base_url

    tts_base = resolve_tts_base_url()
    tts_voice = effective_voice
    tts_lang = str(tts_config.get("lang") or _env("TTS_LANG", "en"))

    from agent.pipeline.tts_http_pool import warmup_tts_connection

    prewarmed_vad = ctx.proc.userdata.get("vad")
    stt_http = await ensure_stt_http_session()
    llm_model = resolve_openrouter_model()
    pipeline_holder[0].configure_providers(
        stt_provider=get_stt_provider(),
        tts_provider=get_tts_provider(),
        tts_voice=tts_voice,
        llm_model=llm_model,
    )

    agent_session, active_stt = _build_session(
        tts_config,
        language=language,
        on_tts_timing=_on_tts_timing,
        pipeline_tracker=pipeline_holder[0],
        on_llm_first_token=_on_llm_first_token,
        vad=prewarmed_vad,
        stt_http_session=stt_http,
    )
    if hasattr(active_stt, "bind_performance_tracker"):
        active_stt.bind_performance_tracker(pipeline_holder[0])
    if hasattr(active_stt, "bind_committed_final_ref"):
        active_stt.bind_committed_final_ref(turn_commit_holder[0].committed_final_ref)

    if barge_in_enabled():
        log_streaming_audit("barge_in_listening_enabled", room=room_name)

    agent_session.llm.on("metrics_collected", _on_llm_metrics_collected)

    async def _warmup_stt_job() -> dict:
        with StepTimer(
            logger,
            operation,
            "stt_warmup",
            room=room_name,
            stt_provider=get_stt_provider(),
        ):
            result = await warmup_stt(
                active_stt,
                language=language,
                http_session=stt_http,
            )
            if not result.get("ok") and not result.get("skipped"):
                logger.warning("STT warmup in job failed: %s", result)
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
            _warmup_stt_job(),
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

    # Now that the caller has actually joined, record their real identity
    # (e.g. "sip_+19106599230" for phone calls) on the CallSession. This
    # covers both the auto-created SIP session above and the browser flow,
    # which creates the session before the identity is known.
    if db_session:
        await _update_session_identity_async(room_name, participant.identity)

    caller_number = _extract_caller_number(participant.identity)
    if caller_number:
        instructions = (
            instructions
            + "\n\n## Caller's phone number\n"
            + f'This caller is calling from {caller_number}. If they say to use '
            + 'this number, their calling number, or "the number I\'m calling '
            + f'from" for WhatsApp, call save_lead_info with whatsapp_number set '
            + f'to exactly "{caller_number}" — do not ask them to read out digits '
            + "in that case."
        )
        log_block(
            logger,
            logging.INFO,
            operation=operation,
            step="caller_number_injected",
            status="OK",
            room=room_name,
            caller_number=caller_number,
        )
        await _prefill_lead_phone_async(room_name, caller_number)
    else:
        # No phone number available for this participant (e.g. a web/browser
        # call — there's no SIP identity to extract a number from). Ask for
        # it once so the post-call summary can still be sent. This ONLY
        # applies when caller_number couldn't be determined — real phone
        # calls always take the branch above and are unaffected by this.
        instructions = (
            instructions
            + "\n\n## Caller's phone number\n"
            + "You were not given this caller's phone number automatically. "
            + "After you have their name and company, ask once for the best "
            + 'number to text a summary and meeting link to — for example, '
            + '"And what\'s the best number to text that meeting link to?" '
            + "Repeat the digits back in a normal spoken format, then call "
            + "save_lead_info with whatsapp_number set to a clean international "
            + "number including the country code (assume Pakistan +92 unless "
            + "they say otherwise).\n"
            + "If the caller says something like \"this is my number\" or "
            + '"I\'m calling from it right now" — this does NOT apply here. '
            + "This is a browser call, not a phone call, so there is no real "
            + "number behind it. Say you're not able to see a number for this "
            + "type of call and ask them to say their actual digits instead. "
            + "Never save a partial number, a country code alone, or anything "
            + "guessed — only call save_lead_info with whatsapp_number once "
            + "they've given you real, complete digits."
        )
        log_block(
            logger,
            logging.INFO,
            operation=operation,
            step="caller_number_not_available",
            status="OK",
            room=room_name,
            participant_identity=participant.identity,
        )

    stt_sample_rate = int(_env("VOICE_AGENT_STT_SAMPLE_RATE", "16000") or "16000")
    room_options = room_io.RoomOptions(
        participant_identity=participant.identity,
        audio_input=resolve_audio_input_options(sample_rate=stt_sample_rate),
    )

    def _open_user_turn(turn_seq: int) -> str:
        turn_id = pipeline_holder[0].begin_pipeline_turn(turn_seq=turn_seq)
        latency_holder[0].turn_id = turn_id
        return turn_id

    def _ensure_user_utterance_open(*, from_vad: bool = False) -> None:
        """Open a pipeline turn for the current user speech segment."""
        if user_utterance_open[0]:
            return
        if from_vad or not vad_speech_active[0]:
            user_turn_seq[0] += 1
        elif user_turn_seq[0] <= 0:
            user_turn_seq[0] = 1
        user_utterance_open[0] = True
        if not pipeline_holder[0].turn_id:
            _open_user_turn(user_turn_seq[0])

    def _cancel_silence_watchdog() -> None:
        task = silence_watchdog_task[0]
        if task is not None and not task.done():
            task.cancel()
        silence_watchdog_task[0] = None

    async def _silence_watchdog() -> None:
        """If the caller goes quiet, check in once, then hang up if they
        still don't respond. Deliberately hardcoded (not LLM-driven) so this
        safety net works every time, independent of tool-calling reliability.
        """
        try:
            await asyncio.sleep(SILENCE_WARNING_S)
            log_block(
                logger,
                logging.INFO,
                operation="VOICE_JOB",
                step="silence_check_in",
                status="TRIGGERED",
                room=room_name,
            )
            check_in_text = (
                "هل ما زلت معي؟"
                if normalize_language(language) == "ar"
                else "Are you still there?"
            )
            handle = agent_session.say(
                check_in_text, allow_interruptions=True, add_to_chat_ctx=True
            )
            await handle.wait_for_playout()

            await asyncio.sleep(SILENCE_DISCONNECT_S)
            log_block(
                logger,
                logging.INFO,
                operation="VOICE_JOB",
                step="silence_auto_disconnect",
                status="TRIGGERED",
                room=room_name,
            )
            farewell_text = (
                "يبدو أنك مشغول الآن، سأنهي المكالمة. لا تتردد بالاتصال مرة أخرى. مع السلامة!"
                if normalize_language(language) == "ar"
                else "I haven't heard from you, so I'll let you go for now. "
                "Feel free to call back anytime. Goodbye!"
            )
            handle = agent_session.say(
                farewell_text, allow_interruptions=True, add_to_chat_ctx=True
            )
            await handle.wait_for_playout()
            await _end_call_async(room_name)
        except asyncio.CancelledError:
            raise
        except Exception:
            import traceback
            logger.error(
                "silence_watchdog failed room=%s\n%s",
                room_name, traceback.format_exc(),
            )

    def _on_vad_speech_start() -> None:
        if not greeting_complete[0]:
            return
        _cancel_silence_watchdog()
        vad_speech_active[0] = True
        _ensure_user_utterance_open(from_vad=True)

    def _on_vad_speech_end() -> None:
        if not greeting_complete[0]:
            return
        vad_speech_active[0] = False
        pipeline = pipeline_holder[0]
        if (
            pipeline.turn_id
            and pipeline.t_user_speech_start is not None
            and pipeline.t_stt_final is None
        ):
            pipeline.emit_stt_latency_summary()
        user_utterance_open[0] = False

    def _on_end_call() -> None:
        _cancel_silence_watchdog()
        call_should_end[0] = True
        log_pipeline_event(
            "END_CALL_REQUESTED",
            room=room_name,
            turn_id=pipeline_holder[0].turn_id,
        )

    agent = VoiceAgent(
        instructions=instructions,
        language=language,
        allow_interruptions=True,
        publisher=publisher,
        pipeline_tracker=pipeline_holder[0],
        on_llm_first_token=_on_llm_first_token,
        on_vad_speech_start=_on_vad_speech_start,
        on_vad_speech_end=_on_vad_speech_end,
        on_end_call=_on_end_call,
        greeting_complete=greeting_complete,
        room=room_name,
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
        if hasattr(active_stt, "update_options"):
            if is_deepgram_provider():
                active_stt.update_options(language=next_lang)
            else:
                fw_lang = "ar" if next_lang.startswith("ar") else "en"
                active_stt.update_options(language=fw_lang)
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

    async def _commit_final_turn(
        *,
        full_text: str,
        turn_seq: int,
        generation: int,
    ) -> None:
        turn_commit = turn_commit_holder[0]
        pipeline = pipeline_holder[0]

        if generation != turn_commit._commit_generation:
            return
        if pipeline.turn_seq != turn_seq:
            return

        pipeline_turn_id = pipeline.turn_id
        display_text = clean_transcript(full_text)
        delta_text = turn_commit.delta_from_incoming(display_text)
        if not delta_text.strip():
            log_pipeline_event(
                "STT_DUPLICATE_FINAL_IGNORED",
                room=room_name,
                turn_id=pipeline_turn_id,
                turn_seq=turn_seq,
                reason="empty_delta_after_prefix",
                raw_chars=len(display_text),
            )
            return

        log_pipeline_event(
            "STT_FINAL_DELTA_EXTRACTED",
            room=room_name,
            turn_id=pipeline_turn_id,
            turn_seq=turn_seq,
            raw_chars=len(display_text),
            final_chars=len(delta_text),
        )

        ok_send, dup_reason = turn_commit.should_send_final(delta_text)
        if not ok_send:
            log_pipeline_event(
                "STT_DUPLICATE_FINAL_IGNORED",
                room=room_name,
                turn_id=pipeline_turn_id,
                turn_seq=turn_seq,
                reason=dup_reason,
                text_preview=delta_text[:80],
            )
            return

        ok, reject_reason = validate_final_transcript(delta_text)
        if not ok:
            pipeline.mark_transcript_rejected(
                reason=reject_reason, transcript_preview=delta_text
            )
            pipeline.reset_turn(room=room_name, phase="full")
            if not vad_speech_active[0]:
                user_utterance_open[0] = False
            return

        if not turn_commit.allow_llm_request(
            turn_seq=turn_seq, pipeline_turn_id=pipeline_turn_id, room=room_name
        ):
            return

        if latency_holder[0].t_speech_end is None and _env_bool(
            "VOICE_AGENT_LATENCY_SYNTHETIC_SPEECH_END", False
        ):
            latency_holder[0].mark_speech_end()
        latency_holder[0].mark_stt_final()

        pipeline.mark_stt_final(transcript_preview=delta_text)
        pipeline.emit_stt_latency_summary()
        pipeline.reset_turn(room=room_name, phase="llm")
        latency_holder[0].turn_id = pipeline.turn_id

        turn_commit.record_committed_final(display_text)
        turn_commit.record_sent_final(delta_text)
        user_final_committed_turns.add(turn_seq)

        log_pipeline_event(
            "USER_FINAL_TRANSCRIPT",
            room=room_name,
            turn_id=pipeline_turn_id,
            turn_seq=turn_seq,
            text_preview=delta_text[:80],
            tts_mode=tts_stream_mode_label(),
        )

        await publisher.agent_state("thinking")
        log_pipeline_event(
            "AGENT_STATE_THINKING",
            room=room_name,
            turn_id=pipeline_turn_id,
            turn_seq=turn_seq,
            reason="user_final_transcript_stable",
        )

        prime_ui = (
            "…"
            if user_ui_english_enabled() and needs_english_ui_translation(delta_text)
            else delta_text.strip()
        )
        log_pipeline_event(
            "TRANSCRIPT_SENT_TO_UI",
            room=room_name,
            turn_id=pipeline_turn_id,
            turn_seq=turn_seq,
            text_preview=prime_ui[:120],
            is_final=True,
        )
        await publisher.user_transcript(
            prime_ui,
            is_final=True,
            message_id=str(uuid.uuid4()),
            turn_seq=turn_seq,
        )

        ui_text = delta_text.strip()
        if user_ui_english_enabled() and needs_english_ui_translation(delta_text):
            ui_text = await translate_to_english(delta_text)
        if ui_text.strip() and ui_text.strip() != prime_ui.strip():
            await publisher.user_transcript(
                ui_text,
                is_final=True,
                message_id=str(uuid.uuid4()),
                turn_seq=turn_seq,
            )
        if ui_text.strip():
            transcript_lines.append(f"USER: {ui_text.strip()}")

        stt_snapshot = pipeline.build_stt_metrics()
        log_pipeline_event(
            "TRANSCRIPT_SENT_TO_LLM",
            room=room_name,
            turn_id=pipeline_turn_id,
            turn_seq=turn_seq,
            text_preview=ui_text[:120],
            stt_speech_end_to_final_ms=stt_snapshot.get("stt_speech_end_to_final_ms"),
            stt_wall_ms=stt_snapshot.get("stt_wall_ms"),
            stt_partial_count=stt_snapshot.get("stt_partial_count"),
            stt_provider=stt_snapshot.get("stt_provider"),
        )

        turn_commit.mark_llm_started(
            turn_seq=turn_seq, pipeline_turn_id=pipeline_turn_id
        )
        last_llm_turn_seq[0] = turn_seq
        if pipeline_turn_id not in llm_dispatch_logged:
            llm_dispatch_logged.add(pipeline_turn_id)
            pipeline.mark_llm_dispatch()
            latency_holder[0].mark_llm_dispatch()
            log_pipeline_event(
                "LLM_START",
                room=room_name,
                turn_id=pipeline_turn_id,
                turn_seq=turn_seq,
                tts_mode=tts_stream_mode_label(),
            )
            _publish_llm_start_milestone()

        await log_call_event(
            room_name,
            "user_transcript",
            {"text": ui_text[:500], "stt_raw": display_text[:500], "stt_delta": delta_text[:500]},
        )
        stt_segment_parts.clear()
        if not vad_speech_active[0]:
            user_utterance_open[0] = False
        log_pipeline_event(
            "TURN_BUFFER_CLEARED",
            room=room_name,
            turn_id=pipeline_turn_id,
            turn_seq=turn_seq,
        )

    @agent_session.on("user_input_transcribed")
    def on_transcript(ev: UserInputTranscribedEvent):
        import asyncio

        if ev.is_final:
            cleaned_final = clean_transcript(ev.transcript)
            if cleaned_final:
                if is_faster_whisper_provider():
                    stt_segment_parts[:] = [cleaned_final]
                else:
                    stt_segment_parts[:] = merge_stt_segment(
                        stt_segment_parts, cleaned_final
                    )

        async def _handle():
            if not greeting_complete[0]:
                return

            component = "STT"
            if ev.is_final:
                display_text = clean_transcript(
                    joined_transcript(stt_segment_parts, interim="")
                )
            else:
                display_text = clean_transcript(ev.transcript)
            if not display_text.strip():
                return

            _ensure_user_utterance_open()

            if ev.is_final:
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
                pipeline_holder[0].record_stt_partial(text_preview=display_text)
                log_pipeline_event(
                    "STT_PARTIAL_TRANSCRIPT",
                    room=room_name,
                    turn_id=pipeline_holder[0].turn_id,
                    turn_seq=user_turn_seq[0],
                    stt_partial_count=pipeline_holder[0].stt_partial_count,
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

            turn_commit = turn_commit_holder[0]
            pipeline_turn_id = pipeline_holder[0].turn_id
            current_turn_seq = user_turn_seq[0]
            raw_full = clean_transcript(
                joined_transcript(stt_segment_parts, interim="")
                if ev.is_final
                else display_text
            )
            delta_text = turn_commit.delta_from_incoming(raw_full)
            if not delta_text.strip():
                delta_text = clean_transcript(ev.transcript)

            if (
                normalize_language(language) == "ar"
                and is_likely_mistranscribed_for_ar_call(raw_full)
            ):
                stt_segment_parts.clear()
                if user_stt_lang_holder[0] != "ar-SA":
                    user_stt_lang_holder[0] = "ar-SA"
                    if is_deepgram_provider() and hasattr(active_stt, "update_options"):
                        active_stt.update_options(language="ar-SA")
                pipeline_holder[0].emit_stt_latency_summary()
                pipeline_holder[0].reset_turn(room=room_name, phase="full")
                log_block(
                    logger,
                    logging.WARNING,
                    operation="STT",
                    step="skip_mistranscribed_user_text",
                    status="SKIP",
                    room=room_name,
                    text_preview=raw_full[:80],
                )
                return

            _route_user_stt_language(raw_full)
            if (
                not ev.is_final
                and normalize_language(language) == "ar"
                and user_stt_lang_holder[0] == "en-US"
                and dominant_user_script(raw_full) == "latin"
                and stt_segment_parts
            ):
                prev = dominant_user_script(joined_transcript(stt_segment_parts, interim=""))
                if prev not in ("latin", "unknown"):
                    stt_segment_parts.clear()

            if ev.is_final:
                turn_seq = current_turn_seq

                if turn_seq in user_final_committed_turns:
                    if transcript_extends_prior(
                        turn_commit.last_committed_final, raw_full
                    ):
                        log_pipeline_event(
                            "STT_FINAL_SUPERSEDES_PRIOR",
                            room=room_name,
                            turn_id=pipeline_turn_id,
                            turn_seq=turn_seq,
                            prior_preview=turn_commit.last_committed_final[:80],
                            text_preview=raw_full[:80],
                        )
                        turn_commit.cancel_pending_commit(
                            reason="superseded_final",
                            turn_id=pipeline_turn_id,
                            room=room_name,
                        )
                        user_final_committed_turns.discard(turn_seq)
                    else:
                        log_pipeline_event(
                            "DUPLICATE_FINAL_TRANSCRIPT_DROPPED",
                            room=room_name,
                            turn_id=pipeline_turn_id,
                            turn_seq=turn_seq,
                            text_preview=raw_full[:80],
                        )
                        return

                turn_commit.schedule_final_commit(
                    turn_id=pipeline_turn_id,
                    room=room_name,
                    turn_seq=turn_seq,
                    full_text=raw_full,
                    commit_fn=lambda **kw: _commit_final_turn(
                        full_text=kw["full_text"],
                        turn_seq=kw["turn_seq"],
                        generation=kw["generation"],
                    ),
                )
                return

            if not delta_text.strip():
                log_pipeline_event(
                    "STT_DUPLICATE_FINAL_IGNORED",
                    room=room_name,
                    turn_id=pipeline_turn_id,
                    turn_seq=user_turn_seq[0],
                    reason="empty_partial_delta",
                    raw_chars=len(raw_full),
                )
                return

            log_pipeline_event(
                "STT_DELTA_EXTRACTED",
                room=room_name,
                turn_id=pipeline_turn_id,
                turn_seq=current_turn_seq,
                raw_chars=len(raw_full),
                delta_chars=len(delta_text),
            )

            ui_text = await user_message_for_ui(delta_text, is_final=False)
            if ui_text is None:
                return

            log_pipeline_event(
                "TRANSCRIPT_SENT_TO_UI",
                room=room_name,
                turn_id=pipeline_turn_id,
                turn_seq=current_turn_seq,
                text_preview=(ui_text or "")[:120],
                is_final=False,
            )
            await publisher.user_transcript(
                ui_text,
                is_final=False,
                turn_seq=current_turn_seq,
            )

        asyncio.create_task(_handle())

    @agent_session.on("agent_state_changed")
    def on_agent_state(ev: AgentStateChangedEvent):
        import asyncio

        def _pipeline_response_active() -> bool:
            p = pipeline_holder[0]
            return p.t_llm_dispatch is not None and p.t_playback_complete is None

        async def _handle():
            state = ev.new_state
            old = ev.old_state
            pipeline = pipeline_holder[0]
            pipeline_turn_id = pipeline.turn_id
            agent_internal_turn_id = latency_holder[0].turn_id

            log_streaming_audit(
                "agent_state_transition",
                turn_id=pipeline_turn_id,
                room=room_name,
                from_state=old,
                to_state=state,
            )
            log_block(
                logger,
                logging.INFO,
                operation="AGENT_STATE",
                step="transition",
                status="OK",
                room=room_name,
                from_state=old,
                to_state=state,
                turn_id=pipeline_turn_id,
                agent_internal_turn_id=agent_internal_turn_id,
            )

            natural_turn_end = state == "listening" and old == "speaking"
            if (
                state == "listening"
                and not natural_turn_end
                and _pipeline_response_active()
                and not barge_in_enabled()
            ):
                log_streaming_audit(
                    "agent_state_listening_suppressed",
                    turn_id=pipeline_turn_id,
                    room=room_name,
                    from_state=old,
                    to_state=state,
                )
                return

            if state == "interrupted" or (state == "thinking" and old == "speaking"):
                await publisher.llm_playback_end(interrupted=True)
            if state == "thinking":
                stt_segment_parts.clear()
                if not vad_speech_active[0]:
                    user_utterance_open[0] = False
                stt_final_ready = pipeline._snap_stt_final is not None
                if not stt_final_ready:
                    log_pipeline_event(
                        "LLM_START_BLOCKED_UNTIL_FINAL",
                        room=room_name,
                        turn_id=pipeline_turn_id,
                        turn_seq=pipeline.turn_seq,
                    )
                elif pipeline_turn_id not in llm_dispatch_logged:
                    llm_dispatch_logged.add(pipeline_turn_id)
                    pipeline.mark_llm_dispatch()
                    latency_holder[0].mark_llm_dispatch()
                    if agent_internal_turn_id != pipeline_turn_id:
                        latency_holder[0].turn_id = pipeline_turn_id
                    log_pipeline_event(
                        "LLM_START",
                        room=room_name,
                        turn_id=pipeline_turn_id,
                        turn_seq=pipeline.turn_seq,
                        agent_internal_turn_id=agent_internal_turn_id,
                        tts_mode=tts_stream_mode_label(),
                    )
                    _publish_llm_start_milestone()
            if state == "speaking":
                latency_holder[0].mark_audio_out()
            if natural_turn_end:
                user_final_committed_turns.clear()
                turn_commit = turn_commit_holder[0]
                turn_commit.llm_started_turn_seqs.clear()
                turn_commit.llm_dispatched_pipeline_turn_ids.clear()
                if turn_commit.last_sent_final_ref is not None:
                    turn_commit.last_sent_final_ref[0] = ""
                llm_dispatch_logged.clear()
                await publisher.llm_playback_end()
                pipeline.mark_playback_complete()
                perf_summary = pipeline.build_summary()
                pipeline.log_turn_summary()
                pipeline.close_pipeline_turn()
                payload = latency_holder[0].to_payload(pipeline_summary=perf_summary)
                log_block(
                    logger,
                    logging.INFO,
                    operation="TURN_LATENCY",
                    step="turn_complete",
                    status="OK",
                    room=room_name,
                    turn_id=pipeline_turn_id,
                    **{k: v for k, v in payload.items() if v is not None and k != "turn_id"},
                )
                await publisher.latency(payload)
                await log_call_event(room_name, "latency", payload)
                latency_holder[0] = TurnLatency()
                pipeline.reset_turn(room=room_name)

                # Restart the silence watchdog after a genuine real-user turn.
                # pipeline_turn_id is only set for turns opened by real STT
                # input — the watchdog's own "Are you still there?" / farewell
                # lines never open a pipeline turn, so this naturally skips
                # restarting after the watchdog's own utterances.
                if pipeline_turn_id and not call_should_end[0]:
                    _cancel_silence_watchdog()
                    silence_watchdog_task[0] = asyncio.create_task(_silence_watchdog())

                # Goodbye audio has now fully finished playing. If the LLM
                # called end_call() during this turn, disconnect the SIP
                # call now instead of waiting for the caller to hang up.
                if call_should_end[0]:
                    log_block(
                        logger,
                        logging.INFO,
                        operation="VOICE_JOB",
                        step="call_auto_disconnect",
                        status="TRIGGERED",
                        room=room_name,
                        turn_id=pipeline_turn_id,
                    )
                    asyncio.create_task(_end_call_async(room_name))
            if state == "listening" and not greeting_complete[0]:
                return

            if state == "listening":
                log_pipeline_event(
                    "AGENT_STATE_LISTENING",
                    room=room_name,
                    turn_id=pipeline_turn_id,
                    from_state=old,
                )
            elif state == "thinking":
                log_pipeline_event(
                    "AGENT_STATE_THINKING",
                    room=room_name,
                    turn_id=pipeline_turn_id,
                    from_state=old,
                )

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
                pipeline_holder[0].assistant_response_length = len(text)
                pipeline_holder[0].mark_llm_complete(response_length=len(text))
                log_block(
                    logger,
                    logging.INFO,
                    operation="LLM",
                    step="assistant_message",
                    status="OK",
                    room=room_name,
                    text=text[:200],
                    response_length=len(text),
                )
                if not call_should_end[0] and _looks_like_goodbye(text):
                    call_should_end[0] = True
                    log_block(
                        logger,
                        logging.INFO,
                        operation="VOICE_JOB",
                        step="goodbye_phrase_detected",
                        status="OK",
                        room=room_name,
                        text_preview=text[:120],
                    )
                transcript_lines.append(f"NORA: {text}")
                await publisher.llm_response(text, is_final=True)
                await log_call_event(
                    room_name,
                    "agent_response",
                    {"text": text[:500]},
                )

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
            # Start the silence watchdog now that the greeting has finished
            # and we're genuinely waiting on the caller for the first time.
            _cancel_silence_watchdog()
            silence_watchdog_task[0] = asyncio.create_task(_silence_watchdog())
        with StepTimer(logger, operation, "agent_session_active", room=room_name, job_id=job_id):
            await _wait_until_room_disconnected(room)
        # Room has ended (caller hung up / disconnected, or the auto-disconnect
        # above ended it). Send the WhatsApp/SMS lead summary now, before
        # marking the session ended, so it still has access to the Lead row
        # for this room. Never allowed to raise — a delivery failure here
        # must not block session teardown.
        _cancel_silence_watchdog()
        await _extract_and_fill_lead_async(room_name, transcript_lines)
        await _send_lead_summary_async(room_name)
        await log_call_event(room_name, "worker_session_ended", {})
        await _mark_session_ended_async(room_name, reason="room_disconnected")
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
        await _mark_session_ended_async(room_name, reason=f"worker_failed: {str(exc)[:200]}")
        await publisher.error("worker_failed", str(exc))
        raise


async def _get_session_async(room_name: str):
    from asgiref.sync import sync_to_async
    from apps.calls.models import CallSession

    with StepTimer(logger, "DATABASE", "fetch_call_session", room=room_name):
        return await sync_to_async(
            lambda: CallSession.objects.select_related("voice_profile")
            .filter(room_name=room_name)
            .first(),
            thread_sensitive=True,
        )()


async def _create_session_async(room_name: str, *, language: str, system_prompt: str = ""):
    """Create a CallSession for a room that has none yet.

    This covers SIP/phone calls: LiveKit's SIP dispatch rule creates the room
    and dispatches this worker directly, bypassing the Django
    /api/calls/start/ endpoint that normally creates the CallSession up front
    for browser calls. Without a matching CallSession row, every
    log_call_event(...) call in this file silently no-ops (see
    agent/pipeline/session_events.py: _persist_call_event returns early when
    it can't find a session for room_name) — so nothing about the call gets
    stored or shown in the admin dashboard.
    """
    from asgiref.sync import sync_to_async
    from apps.calls.models import CallSession

    def _create():
        session, _created = CallSession.objects.get_or_create(
            room_name=room_name,
            defaults={
                "user_identity": "sip-caller",
                "status": CallSession.STATUS_ACTIVE,
                "language": language,
                "system_prompt": system_prompt,
            },
        )
        return session

    with StepTimer(logger, "DATABASE", "create_call_session", room=room_name):
        return await sync_to_async(_create, thread_sensitive=True)()


async def _update_session_identity_async(room_name: str, identity: str) -> None:
    """Record the caller's real participant identity once they've joined
    (e.g. "sip_+19106599230" for phone calls) and ensure status is active.
    """
    from asgiref.sync import sync_to_async
    from apps.calls.models import CallSession

    def _update():
        CallSession.objects.filter(room_name=room_name).update(
            user_identity=identity, status=CallSession.STATUS_ACTIVE
        )

    with StepTimer(logger, "DATABASE", "update_call_session_identity", room=room_name):
        await sync_to_async(_update, thread_sensitive=True)()


async def _prefill_lead_phone_async(room_name: str, phone_number: str) -> None:
    """Pre-populate the Lead's phone number from the caller's real SIP
    identity as soon as they join, so the SMS summary always goes to the
    number they're actually calling from — without depending on the LLM to
    ask for it or transcribe it correctly.

    save_lead_info's whatsapp_number parameter can still override this
    later (e.g. if the caller explicitly gives a different number), since
    _save_lead_fields only updates fields it receives a non-empty value
    for — it won't clobber this prefilled value with nothing.
    """
    from asgiref.sync import sync_to_async
    from apps.calls.models import CallSession, Lead

    def _prefill():
        session = CallSession.objects.filter(room_name=room_name).first()
        if session is None:
            return
        lead, _ = Lead.objects.get_or_create(session=session)
        if not lead.whatsapp_number:
            lead.whatsapp_number = phone_number
            lead.save(update_fields=["whatsapp_number", "updated_at"])

    with StepTimer(logger, "DATABASE", "prefill_lead_phone", room=room_name):
        await sync_to_async(_prefill, thread_sensitive=True)()


async def _extract_and_fill_lead_async(room_name: str, transcript_lines: list[str]) -> None:
    """Post-call backstop for save_lead_info's live tool-calling reliability.

    Runs the accumulated transcript through a single dedicated LLM
    extraction call, then fills in any Lead fields that are still empty —
    never overwrites anything save_lead_info already captured correctly
    live. Deliberately never raises; a failure here must not block sending
    the summary or marking the session ended.
    """
    if not transcript_lines:
        return

    from asgiref.sync import sync_to_async
    from apps.calls.models import CallSession, Lead
    from agent.pipeline.lead_extraction import extract_lead_fields, FIELDS

    transcript = "\n".join(transcript_lines)

    with StepTimer(logger, "LEAD_SUMMARY", "extract_and_fill_lead", room=room_name):
        try:
            extracted = await sync_to_async(extract_lead_fields, thread_sensitive=False)(
                transcript
            )
            if not extracted:
                logger.info(
                    "extract_and_fill_lead: nothing extracted room=%s", room_name
                )
                return

            def _fill():
                session = CallSession.objects.filter(room_name=room_name).first()
                if session is None:
                    return []
                lead, _ = Lead.objects.get_or_create(session=session)
                changed = []
                for field in FIELDS:
                    if field not in extracted:
                        continue
                    current = getattr(lead, field, None)
                    is_empty = current is None or current == ""
                    if is_empty:
                        setattr(lead, field, extracted[field])
                        changed.append(field)
                if changed:
                    lead.save(update_fields=[*changed, "updated_at"])
                return changed

            changed = await sync_to_async(_fill, thread_sensitive=True)()
            logger.info(
                "extract_and_fill_lead: filled fields=%s room=%s",
                changed, room_name,
            )
        except Exception:
            logger.exception("extract_and_fill_lead FAILED room=%s", room_name)


async def _send_lead_summary_async(room_name: str) -> None:
    """Send the post-call lead summary + meeting link over both SMS and
    WhatsApp (sandbox), independently — one channel failing does not block
    the other. Temporary dual-send for client demo purposes; once WhatsApp
    moves off the sandbox to a real approved sender, this can be simplified
    back to a single channel if desired.

    Looks up the Lead row saved during the call (via save_lead_info /
    the caller-number prefill) using the room name, and sends it to the
    caller's whatsapp_number field. Deliberately never raises — a delivery
    failure here must never block marking the CallSession as ended or crash
    the worker's shutdown path.
    """
    from asgiref.sync import sync_to_async
    from apps.calls.models import CallSession, Lead
    from agent.pipeline.lead_summary import send_lead_summary

    def _get_lead():
        session = CallSession.objects.filter(room_name=room_name).first()
        if session is None:
            return None
        return Lead.objects.filter(session=session).first()

    with StepTimer(logger, "LEAD_SUMMARY", "send_lead_summary", room=room_name):
        try:
            lead = await sync_to_async(_get_lead, thread_sensitive=True)()
            if lead is None:
                logger.info(
                    "send_lead_summary: no Lead found for room=%s, skipping", room_name
                )
                return
            if not lead.whatsapp_number:
                logger.warning(
                    "send_lead_summary: Lead has no whatsapp_number, room=%s, lead_id=%s",
                    room_name, lead.pk,
                )
                return

            try:
                await send_lead_summary(lead.whatsapp_number, lead, channel="sms")
            except Exception:
                logger.exception(
                    "send_lead_summary SMS FAILED room=%s lead_id=%s", room_name, lead.pk
                )

            try:
                await send_lead_summary(lead.whatsapp_number, lead, channel="whatsapp")
            except Exception:
                logger.exception(
                    "send_lead_summary WHATSAPP FAILED room=%s lead_id=%s", room_name, lead.pk
                )
        except Exception:
            logger.exception("send_lead_summary FAILED room=%s", room_name)


async def _end_call_async(room_name: str) -> None:
    """Force-disconnect every participant in the room, ending the SIP call.

    Called once the agent's goodbye audio has fully finished playing (see
    the natural_turn_end handling in on_agent_state above). Requires
    LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET to be set — these are
    the same credentials the worker already uses to connect to LiveKit, so
    they should already be present in the environment.

    NOTE: verify these three env var names match what's actually set in
    your .env / worker_env.py — if your project uses different names for
    the API key/secret, update the _env(...) calls below accordingly.
    """
    livekit_url = _env("LIVEKIT_URL")
    api_key = _env("LIVEKIT_API_KEY")
    api_secret = _env("LIVEKIT_API_SECRET")
    if not (livekit_url and api_key and api_secret):
        logger.warning(
            "call_auto_disconnect: missing LiveKit API credentials "
            "(LIVEKIT_URL/LIVEKIT_API_KEY/LIVEKIT_API_SECRET), cannot end room=%s",
            room_name,
        )
        return

    from livekit import api as lk_api

    lk = lk_api.LiveKitAPI(livekit_url, api_key, api_secret)
    try:
        await lk.room.delete_room(lk_api.DeleteRoomRequest(room=room_name))
        logger.info("call_auto_disconnect: room deleted room=%s", room_name)
    except Exception:
        logger.exception("call_auto_disconnect FAILED room=%s", room_name)
    finally:
        await lk.aclose()


async def _mark_session_ended_async(room_name: str, *, reason: str) -> None:
    from asgiref.sync import sync_to_async
    from apps.calls.models import CallSession

    def _mark():
        session = CallSession.objects.filter(room_name=room_name).first()
        if session:
            session.mark_ended(reason)

    with StepTimer(logger, "DATABASE", "mark_call_session_ended", room=room_name):
        await sync_to_async(_mark, thread_sensitive=True)()


def _startup_provider_checks():
    from agent.providers.supertonic_health import check_supertonic_on_startup
    from agent.worker_env import (
        cuda_visible_devices,
        database_url_configured,
        deepgram_remote_client,
        openrouter_remote_client,
        resolve_tts_base_url,
        resolve_tts_health_url,
        tts_health_check_label,
        worker_cpu_only,
        worker_env,
    )

    from agent.pipeline.tts_factory import get_tts_provider

    livekit_url = _env("LIVEKIT_URL")
    if get_tts_provider() == "cartesia":
        tts_result = {"ok": True, "skipped": True, "reason": "using cartesia, no local TTS server"}
    else:
        tts_result = check_supertonic_on_startup()
    health_ok = tts_result.get("health", {}).get("ok")
    log_block(
        logger,
        logging.INFO,
        operation="WORKER",
        step="env_keys_present",
        status="CHECK",
        worker_env=worker_env(),
        worker_cpu_only=worker_cpu_only(),
        cuda_visible_devices=cuda_visible_devices(),
        livekit_url_present=bool(livekit_url),
        livekit_url=livekit_url or "MISSING",
        database_url_configured=database_url_configured(),
        stt_provider=get_stt_provider(),
        stt_ws_url=_env("STT_WS_URL") or "not_used_on_runpod",
        deepgram_remote_client=deepgram_remote_client(),
        openrouter_remote_client=openrouter_remote_client(),
        tts_base_url=resolve_tts_base_url(),
        tts_health_url=resolve_tts_health_url(),
        tts_health_check=tts_health_check_label(health_ok),
        tts_warmup_ok=tts_result.get("ok"),
        tts_provider=get_tts_provider(),
        tts_voice=_env("TTS_VOICE", "M1"),
        tts_lang=_env("TTS_LANG", "en"),
        openrouter_model=resolve_openrouter_model(),
    )


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