"""Log provider key presence on API startup (matches root .env)."""

from __future__ import annotations

import logging

import os

from django.conf import settings

from agent.pipeline.stt_config import get_stt_provider, is_deepgram_provider, validate_stt_env
from config.step_log import log_block

logger = logging.getLogger("apps.calls.services")


def log_provider_env_status():
    def _hint(key: str) -> str:
        val = getattr(settings, key, "") or os.environ.get(key, "")
        if not val:
            return "MISSING"
        return f"set (len={len(val)})"

    stt_missing = validate_stt_env()
    log_block(
        logger,
        logging.INFO,
        operation="API_STARTUP",
        step="provider_env",
        status="CHECK",
        STT_PROVIDER=get_stt_provider(),
        STT_CONFIG_OK="yes" if not stt_missing else "no",
        STT_MISSING=",".join(stt_missing) if stt_missing else "none",
        DEEPGRAM_API_KEY=_hint("DEEPGRAM_API_KEY")
        if is_deepgram_provider()
        else "not_required",
        OPENROUTER_API_KEY=_hint("OPENROUTER_API_KEY"),
        TTS_BASE_URL=_hint("TTS_BASE_URL"),
        TTS_VOICE=_hint("TTS_VOICE"),
        TTS_LANG=settings.TTS_LANG or "en",
        TTS_PROVIDER=getattr(settings, "TTS_PROVIDER", "multilingual"),
        TTS_MODEL=settings.TTS_MODEL or "multilingual",
        OPENROUTER_BASE_URL=settings.OPENROUTER_BASE_URL or "MISSING",
        LIVEKIT_URL=settings.LIVEKIT_URL or "MISSING",
        note="Provider keys should match repo root .env; LiveKit uses voice_agent_backend/.env only",
    )
