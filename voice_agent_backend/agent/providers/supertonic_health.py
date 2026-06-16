"""TTS health probe on worker startup (multilingual or Supertonic)."""

from __future__ import annotations

import logging
import urllib.error
import urllib.request

from agent.pipeline.tts_factory import get_tts_provider
from agent.pipeline.tts_http_pool import warmup_tts_connection_sync
from agent.worker_env import (
    resolve_tts_base_url,
    resolve_tts_health_url,
    worker_env,
)
from config.step_log import StepTimer, log_block

logger = logging.getLogger("agent.providers.tts")

DEFAULT_VOICE = "M1"
DEFAULT_LANG = "en"


def probe_tts_health_sync() -> dict:
    """Lightweight GET health check (no synthesis)."""
    health_url = resolve_tts_health_url()
    try:
        with urllib.request.urlopen(health_url, timeout=10) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:500]
            return {
                "ok": 200 <= resp.status < 300,
                "status": resp.status,
                "health_url": health_url,
                "body_preview": body,
            }
    except urllib.error.HTTPError as exc:
        return {
            "ok": False,
            "status": exc.code,
            "health_url": health_url,
            "error": str(exc)[:200],
        }
    except Exception as exc:
        return {"ok": False, "health_url": health_url, "error": str(exc)[:200]}


def check_supertonic_on_startup() -> dict:
    base_url = resolve_tts_base_url()
    health_url = resolve_tts_health_url()
    import os

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
            worker_env=worker_env(),
        )
        return {"ok": False, "reason": "missing_url"}

    health = probe_tts_health_sync()
    log_block(
        logger,
        logging.INFO if health.get("ok") else logging.ERROR,
        operation="TTS",
        step="health_check",
        status="OK" if health.get("ok") else "FAIL",
        worker_env=worker_env(),
        health_url=health_url,
        http_status=health.get("status"),
        error=health.get("error", ""),
    )

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
                    health_ok=health.get("ok"),
                    **{k: v for k, v in result.items() if k != "ok"},
                )
                return {**result, "health": health}

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
                health_ok=health.get("ok"),
                handshake_ms=result.get("handshake_ms"),
                bytes_received=result.get("bytes_received"),
            )
            return {**result, "health": health}
    except Exception as exc:
        log_block(
            logger,
            logging.ERROR,
            operation="TTS",
            step="startup_probe",
            status="FAIL",
            provider=provider,
            base_url=base_url,
            health_ok=health.get("ok"),
            error=str(exc)[:400],
        )
        return {"ok": False, "error": str(exc), "health": health}
