"""Create LiveKit rooms and dispatch the voice agent worker (single agent per room)."""

from __future__ import annotations

import asyncio
import json
import logging
import os

from django.conf import settings

from config.step_log import StepTimer, log_block

logger = logging.getLogger("apps.calls.services")


def _api_url() -> str:
    """LiveKit server API expects https/http, not wss."""
    url = (settings.LIVEKIT_URL or os.environ.get("LIVEKIT_URL", "")).strip()
    if url.startswith("wss://"):
        return "https://" + url[len("wss://") :]
    if url.startswith("ws://"):
        return "http://" + url[len("ws://") :]
    return url


async def _ensure_room_and_dispatch_async(
    *,
    room_name: str,
    call_id: str,
    system_prompt: str,
    persona_id: str = "",
    language: str = "en",
) -> dict:
    from livekit import api
    from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest
    from livekit.protocol.room import CreateRoomRequest

    agent_name = getattr(settings, "VOICE_AGENT_NAME", None) or os.environ.get(
        "VOICE_AGENT_NAME", "voice-agent"
    )
    from agent.pipeline.voice_presets import resolve_tts_config

    tts_config = resolve_tts_config(persona_id, language=language)
    metadata = json.dumps(
        {
            "call_id": call_id,
            "system_prompt": (system_prompt or "")[:2000],
            "persona_id": persona_id or "",
            "language": language,
            "tts": tts_config,
        }
    )

    api_url = _api_url()
    lk = api.LiveKitAPI(
        url=api_url,
        api_key=settings.LIVEKIT_API_KEY,
        api_secret=settings.LIVEKIT_API_SECRET,
    )

    try:
        try:
            with StepTimer(
                logger,
                "LIVEKIT",
                "create_room",
                room=room_name,
                agent_name=agent_name,
            ):
                # Room only — do NOT set agents=[] here (that + create_dispatch = 2 agents).
                await lk.room.create_room(
                    CreateRoomRequest(
                        name=room_name,
                        empty_timeout=600,
                        max_participants=10,
                    )
                )
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" in msg or "409" in msg:
                log_block(
                    logger,
                    logging.WARNING,
                    operation="LIVEKIT",
                    step="create_room",
                    status="SKIP",
                    room=room_name,
                    reason="room_already_exists",
                )
            else:
                raise

        with StepTimer(
            logger,
            "LIVEKIT",
            "list_agent_dispatch",
            room=room_name,
        ):
            existing = await lk.agent_dispatch.list_dispatch(room_name)
            for d in existing:
                if d.agent_name == agent_name:
                    log_block(
                        logger,
                        logging.WARNING,
                        operation="LIVEKIT",
                        step="create_agent_dispatch",
                        status="SKIP",
                        room=room_name,
                        reason="agent_already_dispatched",
                        dispatch_id=d.id,
                        agent_name=agent_name,
                    )
                    return {"dispatch_id": d.id, "room_name": room_name, "reused": True}

        with StepTimer(
            logger,
            "LIVEKIT",
            "create_agent_dispatch",
            room=room_name,
            agent_name=agent_name,
        ):
            dispatch = await lk.agent_dispatch.create_dispatch(
                CreateAgentDispatchRequest(
                    agent_name=agent_name,
                    room=room_name,
                    metadata=metadata,
                )
            )
            log_block(
                logger,
                logging.INFO,
                operation="LIVEKIT",
                step="dispatch_created",
                status="OK",
                room=room_name,
                dispatch_id=dispatch.id,
                agent_name=agent_name,
                note="single_dispatch_only",
            )
            return {"dispatch_id": dispatch.id, "room_name": room_name, "reused": False}
    finally:
        await lk.aclose()


def ensure_room_and_dispatch_agent(
    *,
    room_name: str,
    call_id: str,
    system_prompt: str = "",
    persona_id: str = "",
    language: str = "en",
) -> dict:
    """Sync wrapper for Django views."""
    try:
        return asyncio.run(
            _ensure_room_and_dispatch_async(
                room_name=room_name,
                call_id=call_id,
                system_prompt=system_prompt,
                persona_id=persona_id,
                language=language,
            )
        )
    except Exception as exc:
        log_block(
            logger,
            logging.ERROR,
            operation="LIVEKIT",
            step="ensure_room_and_dispatch",
            status="FAIL",
            room=room_name,
            call_id=call_id,
            error=str(exc)[:500],
        )
        raise
