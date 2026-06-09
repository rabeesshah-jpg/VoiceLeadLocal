"""TTS handshake from the Django API process (multilingual or Supertonic)."""

from __future__ import annotations

import logging

from django.conf import settings

from agent.pipeline.tts_factory import get_tts_provider
from agent.pipeline.tts_http_pool import warmup_tts_connection_sync
from agent.pipeline.voice_presets import resolve_tts_config
from config.step_log import StepTimer, log_block

logger = logging.getLogger("apps.calls.services")


def _resolve_voice_lang(persona_id: str = "", *, language: str = "en") -> tuple[str, str]:
    cfg = resolve_tts_config(persona_id, language=language)
    voice = str(
        cfg.get("voice")
        or cfg.get("predefined_voice_id")
        or getattr(settings, "TTS_VOICE", "")
        or getattr(settings, "VOICE_AGENT_CHATTERBOX_VOICE_ID", "")
        or "female"
    )
    lang = str(cfg.get("lang") or getattr(settings, "TTS_LANG", "en") or "en")
    return voice, lang


def run_supertonic_handshake(
    *,
    persona_id: str = "",
    language: str = "en",
    force: bool = False,
    voice_override: str = "",
) -> dict:
    if getattr(settings, "VOICE_AGENT_SKIP_TTS_WARMUP", False):
        return {"ok": True, "skipped": True, "reason": "VOICE_AGENT_SKIP_TTS_WARMUP"}

    base_url = (
        getattr(settings, "TTS_BASE_URL", "")
        or getattr(settings, "CHATTERBOX_TTS_URL", "")
    ).strip()
    if not base_url:
        return {"ok": False, "reason": "TTS_BASE_URL missing"}

    voice, lang = _resolve_voice_lang(persona_id, language=language)
    if voice_override:
        voice = voice_override
    operation = "TTS_HANDSHAKE"
    with StepTimer(
        logger,
        operation,
        "health_and_tts_warmup",
        provider=get_tts_provider(),
        base_url=base_url,
        voice=voice,
        lang=lang,
        force=force,
    ):
        try:
            result = warmup_tts_connection_sync(
                base_url, voice=voice, lang=lang, force=force
            )
        except Exception as exc:
            log_block(
                logger,
                logging.ERROR,
                operation=operation,
                step="health_and_tts_warmup",
                status="FAIL",
                error=str(exc)[:400],
            )
            return {"ok": False, "error": str(exc)}

    status = "OK" if result.get("ok") else "FAIL"
    log_block(
        logger,
        logging.INFO if result.get("ok") else logging.ERROR,
        operation=operation,
        step="health_and_tts_warmup",
        status=status,
        voice=voice,
        lang=lang,
        skipped=result.get("skipped"),
        handshake_ms=result.get("handshake_ms"),
    )
    return result


def run_supertonic_handshake_on_api_startup() -> None:
    run_supertonic_handshake(force=False)
