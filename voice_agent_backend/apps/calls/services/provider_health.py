"""Log provider key presence on API startup (matches root .env)."""

from __future__ import annotations

import logging

from django.conf import settings

from config.step_log import log_block

logger = logging.getLogger("apps.calls.services")


def log_provider_env_status():
    def _hint(key: str) -> str:
        val = getattr(settings, key, "") or ""
        if not val:
            return "MISSING"
        return f"set (len={len(val)})"

    log_block(
        logger,
        logging.INFO,
        operation="API_STARTUP",
        step="provider_env",
        status="CHECK",
        DEEPGRAM_API_KEY=_hint("DEEPGRAM_API_KEY"),
        OPENROUTER_API_KEY=_hint("OPENROUTER_API_KEY"),
        CARTESIA_API_KEY=_hint("CARTESIA_API_KEY"),
        OPENROUTER_BASE_URL=settings.OPENROUTER_BASE_URL or "MISSING",
        LIVEKIT_URL=settings.LIVEKIT_URL or "MISSING",
        note="Provider keys should match repo root .env; LiveKit uses voice_agent_backend/.env only",
    )
