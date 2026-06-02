"""Call session helpers and provider validation."""

from __future__ import annotations

import logging
import os
import uuid

from django.conf import settings

from agent.pipeline.stt_config import validate_stt_env
from agent.prompts import get_voice_agent_instructions
from apps.calls.models import CallEvent, CallSession
from config.step_log import StepTimer

logger = logging.getLogger("apps.calls.services")


class SessionRegistry:
    REQUIRED_PROVIDER_VARS = (
        "OPENROUTER_API_KEY",
        "TTS_BASE_URL",
    )

    @classmethod
    def _tts_base_configured(cls) -> bool:
        return bool(getattr(settings, "TTS_BASE_URL", None)) or bool(
            getattr(settings, "CHATTERBOX_TTS_URL", None)
        )

    @classmethod
    def validate_providers(cls) -> list[str]:
        with StepTimer(logger, "PROVIDERS", "validate_env"):
            missing = []
            for var in cls.REQUIRED_PROVIDER_VARS:
                if var == "TTS_BASE_URL":
                    if not cls._tts_base_configured() and not os.environ.get(
                        "TTS_BASE_URL"
                    ) and not os.environ.get("CHATTERBOX_TTS_URL"):
                        missing.append(var)
                    continue
                if not getattr(settings, var, None) and not os.environ.get(var):
                    missing.append(var)

            missing.extend(validate_stt_env())
            return missing

    @classmethod
    def create_session(
        cls,
        *,
        persona_id: str = "",
        system_prompt: str = "",
        language: str = "en",
    ) -> CallSession:
        with StepTimer(
            logger,
            "SESSION",
            "create_db_row",
            persona_id=persona_id or "default",
            language=language,
        ):
            call_id = uuid.uuid4()
            room_name = f"va-{call_id.hex[:12]}"
            user_identity = f"user-{call_id}"
            prompt = system_prompt.strip() or get_voice_agent_instructions(language)
            session = CallSession.objects.create(
                id=call_id,
                room_name=room_name,
                user_identity=user_identity,
                persona_id=persona_id or "",
                language=language,
                system_prompt=prompt,
                status=CallSession.STATUS_CREATED,
            )
            cls.log_event(
                session,
                "session_created",
                {"room_name": room_name, "language": language},
            )
            return session

    @classmethod
    def log_event(cls, session: CallSession, event_type: str, payload: dict | None = None):
        return CallEvent.objects.create(
            session=session,
            event_type=event_type,
            payload=payload or {},
        )

    @classmethod
    def activate_session(cls, session: CallSession, expires_at):
        with StepTimer(
            logger,
            "SESSION",
            "activate",
            call_id=str(session.id),
            room=session.room_name,
        ):
            session.status = CallSession.STATUS_ACTIVE
            session.token_expires_at = expires_at
            session.save(update_fields=["status", "token_expires_at", "updated_at"])
            cls.log_event(
                session,
                "session_active",
                {"expires_at": expires_at.isoformat()},
            )
