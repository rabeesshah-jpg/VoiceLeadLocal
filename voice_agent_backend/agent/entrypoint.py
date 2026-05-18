"""
LiveKit voice agent worker entrypoint.

Run: python -m agent dev
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

from config.logging_setup import setup_worker_logging

setup_worker_logging(_BACKEND_ROOT)

import django

django.setup()

from livekit.agents import JobContext, WorkerOptions, cli
from livekit.agents.voice import Agent, AgentSession
from livekit.agents.voice.events import (
    AgentStateChangedEvent,
    ConversationItemAddedEvent,
    ErrorEvent,
    UserInputTranscribedEvent,
)
from livekit.plugins import cartesia, deepgram, openai, silero

from agent.observability.latency import TurnLatency
from agent.observability.structured_log import log_event
from agent.pipeline.data_channel import DataChannelPublisher
from agent.pipeline.session_events import log_call_event
from config.step_log import StepTimer, log_block

logger = logging.getLogger("agent.entrypoint")

FALLBACK_APOLOGY = (
    "I'm having a little trouble right now. Could you say that again?"
)


def _env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _build_session(system_prompt: str) -> AgentSession:
    operation = "BUILD_PIPELINE"
    with StepTimer(logger, operation, "load_vad"):
        vad = silero.VAD.load()

    with StepTimer(logger, operation, "init_deepgram_stt", model="nova-2"):
        stt = deepgram.STT(
            model="nova-2",
            api_key=_env("DEEPGRAM_API_KEY") or None,
            interim_results=True,
            punctuate=True,
            smart_format=True,
            sample_rate=16000,
            endpointing_ms=700,
            vad_events=True,
        )

    llm_model = _env("VOICE_AGENT_LLM_MODEL", "openai/gpt-4o-mini")
    with StepTimer(logger, operation, "init_openrouter_llm", model=llm_model):
        llm = openai.LLM(
            model=llm_model,
            api_key=_env("OPENROUTER_API_KEY") or None,
            base_url=_env("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1").rstrip("/"),
            temperature=0.7,
            max_completion_tokens=256,
        )

    cartesia_voice = _env("VOICE_AGENT_CARTESIA_VOICE_ID", "0ad65e7f-006c-47cf-bd31-52279d487913")
    with StepTimer(logger, operation, "init_cartesia_tts", voice_id=cartesia_voice):
        tts = cartesia.TTS(
            api_key=_env("CARTESIA_API_KEY") or None,
            model="sonic-3",
            voice=cartesia_voice,
            sample_rate=24000,
            encoding="pcm_s16le",
        )

    with StepTimer(logger, operation, "assemble_agent_session"):
        return AgentSession(
            vad=vad,
            stt=stt,
            llm=llm,
            tts=tts,
            allow_interruptions=True,
            min_interruption_duration=0.2,
            preemptive_generation=True,
            min_endpointing_delay=0.5,
            max_endpointing_delay=1.3,
        )


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
    system_prompt = _env(
        "VOICE_AGENT_SYSTEM_PROMPT",
        "You are a helpful voice assistant. Keep replies concise and conversational.",
    )

    with StepTimer(logger, operation, "load_session_prompt", room=room_name):
        try:
            db_session = await _get_session_async(room_name)
            if db_session and db_session.system_prompt:
                system_prompt = db_session.system_prompt
                log_block(
                    logger,
                    logging.INFO,
                    operation=operation,
                    step="load_session_prompt",
                    status="OK",
                    room=room_name,
                    prompt_source="database",
                    prompt_len=len(system_prompt),
                )
        except Exception as exc:
            logger.warning("Session prompt lookup skipped: %s", exc)

    log_call_event(room_name, "worker_joined", {"job_id": job_id})
    await publisher.agent_state("listening")

    agent_session = _build_session(system_prompt)
    agent = Agent(instructions=system_prompt, allow_interruptions=True)

    @agent_session.on("user_input_transcribed")
    def on_transcript(ev: UserInputTranscribedEvent):
        import asyncio

        async def _handle():
            component = "STT"
            if ev.is_final:
                latency_holder[0].mark_stt_final()
                log_block(
                    logger,
                    logging.INFO,
                    operation=component,
                    step="final_transcript",
                    status="OK",
                    room=room_name,
                    text=ev.transcript[:200],
                    is_final=True,
                )
            else:
                log_block(
                    logger,
                    logging.DEBUG,
                    operation=component,
                    step="partial_transcript",
                    status="OK",
                    room=room_name,
                    text=ev.transcript[:120],
                    is_final=False,
                )
            # UI: finals only (partials cause duplicate lines when multiple agents were active)
            if ev.is_final:
                await publisher.user_transcript(ev.transcript, is_final=True)
            if ev.is_final and ev.transcript.strip():
                log_call_event(
                    room_name,
                    "user_transcript",
                    {"text": ev.transcript[:500]},
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
            if state == "thinking":
                latency_holder[0].mark_llm_first_token()
            if state == "speaking":
                latency_holder[0].mark_tts_first_byte()
                latency_holder[0].mark_audio_out()
            if state == "listening" and old == "speaking":
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
                log_call_event(room_name, "latency", payload)
                latency_holder[0] = TurnLatency()
            await publisher.agent_state(state)
            log_call_event(room_name, "agent_state", {"state": state, "old_state": old})

        asyncio.create_task(_handle())

    @agent_session.on("conversation_item_added")
    def on_conversation_item(ev: ConversationItemAddedEvent):
        import asyncio

        async def _handle():
            item = ev.item
            if item.role != "assistant":
                return
            text = item.text_content or ""
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
                await publisher.agent_text(text, is_final=True)

        asyncio.create_task(_handle())

    @agent_session.on("error")
    def on_error(ev: ErrorEvent):
        import asyncio

        async def _handle():
            err = ev.error
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
            log_call_event(room_name, "error", {"code": code, "message": str(err)[:300]})
            try:
                with StepTimer(logger, operation, "fallback_tts_apology", room=room_name):
                    await agent_session.say(FALLBACK_APOLOGY, allow_interruptions=True)
            except Exception as say_exc:
                logger.warning("Fallback say failed: %s", say_exc)

        asyncio.create_task(_handle())

    try:
        with StepTimer(logger, operation, "agent_session_run", room=room_name, job_id=job_id):
            await agent_session.start(agent=agent, room=room)
        log_call_event(room_name, "worker_session_ended", {})
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
        log_call_event(room_name, "worker_failed", {"error": str(exc)[:300]})
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
    from agent.providers.cartesia_health import check_cartesia_on_startup

    log_block(
        logger,
        logging.INFO,
        operation="WORKER",
        step="env_keys_present",
        status="CHECK",
        deepgram=bool(_env("DEEPGRAM_API_KEY")),
        openrouter=bool(_env("OPENROUTER_API_KEY")),
        cartesia=bool(_env("CARTESIA_API_KEY")),
        openrouter_model=_env("VOICE_AGENT_LLM_MODEL", "openai/gpt-4o-mini"),
        livekit_url=_env("LIVEKIT_URL"),
    )
    check_cartesia_on_startup()


def main():
    log_path = setup_worker_logging(_BACKEND_ROOT)
    log_block(
        logger,
        logging.INFO,
        operation="WORKER",
        step="startup",
        status="OK",
        log_file=str(log_path),
        agent_name=_env("VOICE_AGENT_NAME", "voice-agent"),
    )
    _startup_provider_checks()
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=_env("VOICE_AGENT_NAME", "voice-agent"),
        )
    )


if __name__ == "__main__":
    main()
