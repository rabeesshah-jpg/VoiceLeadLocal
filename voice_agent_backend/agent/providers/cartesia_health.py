"""Cartesia API health / credit probe (logs only; no secrets)."""

from __future__ import annotations

import logging
import os
import time

from config.step_log import StepTimer, log_block

logger = logging.getLogger("agent.providers.cartesia")


def check_cartesia_on_startup() -> dict:
    """
    Probe Cartesia with a minimal TTS request to verify key + credits.
    Returns status dict for logging.
    """
    api_key = (os.environ.get("CARTESIA_API_KEY") or "").strip()
    voice_id = os.environ.get(
        "VOICE_AGENT_CARTESIA_VOICE_ID",
        "0ad65e7f-006c-47cf-bd31-52279d487913",
    )

    if not api_key:
        log_block(
            logger,
            logging.ERROR,
            operation="CARTESIA",
            step="startup_probe",
            status="FAIL",
            reason="CARTESIA_API_KEY missing",
        )
        return {"ok": False, "reason": "missing_api_key"}

    key_hint = f"{api_key[:8]}…{api_key[-4:]}" if len(api_key) > 12 else "(short)"

    try:
        with StepTimer(
            logger,
            "CARTESIA",
            "startup_tts_probe",
            key_hint=key_hint,
            voice_id=voice_id,
        ):
            import cartesia

            client = cartesia.Cartesia(api_key=api_key)
            t0 = time.perf_counter()
            chunks = 0
            bytes_received = 0
            first_byte_ms = None

            for event in client.tts.generate_sse(
                model_id="sonic-3",
                transcript="ok",
                voice={"mode": "id", "id": voice_id},
                output_format={
                    "container": "raw",
                    "encoding": "pcm_s16le",
                    "sample_rate": 24000,
                },
            ):
                audio = getattr(event, "audio", None) or (
                    event.get("audio") if isinstance(event, dict) else None
                )
                if audio:
                    if first_byte_ms is None:
                        first_byte_ms = (time.perf_counter() - t0) * 1000
                    chunks += 1
                    bytes_received += len(audio)
                if chunks >= 3:
                    break

            log_block(
                logger,
                logging.INFO,
                operation="CARTESIA",
                step="startup_probe",
                status="OK",
                key_hint=key_hint,
                voice_id=voice_id,
                chunks=chunks,
                bytes_received=bytes_received,
                first_byte_ms=round(first_byte_ms or 0, 2),
                credits_note="TTS bytes returned — key valid; check Cartesia dashboard if calls fail",
            )
            return {
                "ok": True,
                "chunks": chunks,
                "bytes_received": bytes_received,
                "first_byte_ms": first_byte_ms,
            }

    except Exception as exc:
        err = str(exc).lower()
        reason = "unknown_error"
        if "401" in err or "unauthorized" in err:
            reason = "invalid_api_key"
        elif "402" in err or "payment" in err or "credit" in err or "balance" in err:
            reason = "credits_or_billing"
        elif "403" in err:
            reason = "forbidden"

        log_block(
            logger,
            logging.ERROR,
            operation="CARTESIA",
            step="startup_probe",
            status="FAIL",
            key_hint=key_hint,
            voice_id=voice_id,
            reason=reason,
            error=str(exc)[:400],
            credits_note=(
                "If reason=credits_or_billing, top up at https://play.cartesia.ai"
                if reason == "credits_or_billing"
                else "Verify CARTESIA_API_KEY matches root .env"
            ),
        )
        return {"ok": False, "reason": reason, "error": str(exc)}
