"""TTS health probe on worker startup (multilingual or Supertonic)."""

from __future__ import annotations

import logging
import os

from agent.pipeline.tts_factory import get_tts_provider
from agent.pipeline.tts_http_pool import warmup_tts_connection_sync
from config.step_log import StepTimer, log_block

logger = logging.getLogger("agent.providers.tts")

DEFAULT_URL = "http://157.157.221.29:30039"
DEFAULT_VOICE = "female"
DEFAULT_LANG = "en"


def check_supertonic_on_startup() -> dict:
    base_url = (
        os.environ.get("TTS_BASE_URL")
        or os.environ.get("CHATTERBOX_TTS_URL")
        or DEFAULT_URL
    ).strip()
    base_url = base_url.rstrip("/")
    voice = os.environ.get("TTS_VOICE") or os.environ.get(
        "VOICE_AGENT_CHATTERBOX_VOICE_ID", DEFAULT_VOICE
    )
    lang = os.environ.get("TTS_LANG", DEFAULT_LANG)

    if not base_url:
        log_block(
            logger,
            logging.WARNING,
            operation="TTS",
            step="startup_probe",
            status="SKIP",
            reason="TTS_BASE_URL missing",
        )
        return {"ok": False, "reason": "missing_url"}

    provider = get_tts_provider()
    try:
        with StepTimer(
            logger,
            "TTS",
            "startup_connection_warmup",
            provider=provider,
            base_url=base_url,
            voice=voice,
            lang=lang,
        ):
            result = warmup_tts_connection_sync(
                base_url, voice=voice, lang=lang, force=False
            )
            if not result.get("ok"):
                log_block(
                    logger,
                    logging.ERROR,
                    operation="TTS",
                    step="startup_probe",
                    status="FAIL",
                    provider=provider,
                    base_url=base_url,
                    voice=voice,
                    lang=lang,
                    **{k: v for k, v in result.items() if k != "ok"},
                )
                return result

            log_block(
                logger,
                logging.INFO,
                operation="TTS",
                step="startup_probe",
                status="OK",
                provider=provider,
                base_url=base_url,
                voice=voice,
                lang=lang,
                handshake_ms=result.get("handshake_ms"),
                bytes_received=result.get("bytes_received"),
            )
            return result
    except Exception as exc:
        log_block(
            logger,
            logging.ERROR,
            operation="TTS",
            step="startup_probe",
            status="FAIL",
            provider=provider,
            base_url=base_url,
            error=str(exc)[:400],
        )
        return {"ok": False, "error": str(exc)}
