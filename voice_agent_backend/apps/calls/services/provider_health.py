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
    database_url = (getattr(settings, "DATABASES", {}).get("default", {}).get("ENGINE", ""))
    using_postgres = "postgresql" in database_url or "postgis" in database_url
    log_block(
        logger,
        logging.INFO,
        operation="API_STARTUP",
        step="provider_env",
        status="CHECK",
        role="django_api",
        database_engine=database_url or "unknown",
        database_url_configured=using_postgres,
        LIVEKIT_URL=settings.LIVEKIT_URL or "MISSING",
        LIVEKIT_API_KEY=_hint("LIVEKIT_API_KEY"),
        STT_PROVIDER=get_stt_provider(),
        STT_CONFIG_OK="yes" if not stt_missing else "no",
        STT_MISSING=",".join(stt_missing) if stt_missing else "none",
        DEEPGRAM_API_KEY=_hint("DEEPGRAM_API_KEY")
        if is_deepgram_provider()
        else "worker_only",
        OPENROUTER_API_KEY=_hint("OPENROUTER_API_KEY") + " (worker_only)",
        TTS_BASE_URL=_hint("TTS_BASE_URL") + " (worker_only; optional on API)",
        note="Realtime STT/LLM/TTS run on LiveKit worker; Django orchestrates LiveKit + sessions",
    )
