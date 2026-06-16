"""RunPod Supertonic voice cloning API client (no local model logic)."""

from __future__ import annotations

import logging
import time
from pathlib import Path

import httpx
from django.conf import settings

from config.step_log import log_block

logger = logging.getLogger("apps.calls.services.tts_voice_clone")


def _poll_timeout_s() -> float:
    return float(getattr(settings, "TTS_CLONE_POLL_TIMEOUT_SECONDS", 90))


def _poll_interval_s() -> float:
    return float(getattr(settings, "TTS_CLONE_POLL_INTERVAL_SECONDS", 2))


def _clone_timeout_s() -> float:
    return float(getattr(settings, "TTS_CLONE_TIMEOUT_SECONDS", 120))


def _tts_timeout_s() -> float:
    return float(getattr(settings, "TTS_TIMEOUT", 60))


def resolve_voice_profile_tts_base_url() -> str:
    """RunPod TTS URL for Django voice-profile admin (public URL from local Django)."""
    return (
        getattr(settings, "VOICE_PROFILE_TTS_BASE_URL", "")
        or getattr(settings, "TTS_BASE_URL", "")
        or getattr(settings, "CHATTERBOX_TTS_URL", "")
    ).strip().rstrip("/")


def _base_url() -> str:
    return resolve_voice_profile_tts_base_url()


def _clone_endpoint() -> str:
    path = getattr(settings, "TTS_CLONE_ENDPOINT", "/v1/voices/clone").strip()
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def _upload_endpoint() -> str:
    return "/v1/voices"


def _synth_endpoint_path() -> str:
    path = getattr(settings, "TTS_SYNTH_ENDPOINT", "/v1/tts").strip()
    if not path.startswith("/"):
        path = f"/{path}"
    return path


def _tts_model() -> str:
    return str(getattr(settings, "TTS_MODEL", "supertonic-3") or "supertonic-3")


def _tts_lang() -> str:
    return str(getattr(settings, "TTS_LANG", "en") or "en")


def _extract_error_detail(body: dict, fallback: str = "") -> str:
    if body.get("error"):
        err = body["error"]
        if isinstance(err, dict):
            return str(err.get("message") or err.get("code") or fallback)
        return str(err)
    detail = body.get("detail")
    if isinstance(detail, list):
        return "; ".join(
            str(x.get("msg", x)) if isinstance(x, dict) else str(x) for x in detail
        )
    if detail:
        return str(detail)
    return fallback


def parse_runpod_voice_response(body: dict) -> dict:
    """
    Normalize RunPod voice API JSON.

    POST /v1/tts expects ``voice_id`` = RunPod profile UUID (not engine_voice_name).
    We store that UUID in provider_voice_id when status is ``ready``.
    """
    if body.get("error"):
        return {
            "runpod_voice_uuid": "",
            "provider_voice_id": "",
            "status": "failed",
            "status_message": _extract_error_detail(body),
            "raw": body,
        }

    metadata = body.get("metadata") or {}
    voice_id = str(
        body.get("voice_id")
        or metadata.get("voice_id")
        or ""
    ).strip()
    status = str(body.get("status") or metadata.get("status") or "").strip()
    status_message = str(
        metadata.get("status_message") or body.get("status_message") or ""
    ).strip()
    engine_voice = str(
        metadata.get("engine_voice_name")
        or body.get("engine_voice_name")
        or metadata.get("provider_voice_id")
        or body.get("provider_voice_id")
        or ""
    ).strip()

    cloning_supported = metadata.get("cloning_supported")
    if cloning_supported is None:
        cloning_supported = body.get("cloning_supported")
    tts_usable = metadata.get("tts_usable")
    if tts_usable is None:
        tts_usable = body.get("tts_usable")
    if tts_usable is None and cloning_supported is not None:
        tts_usable = cloning_supported

    provider_voice_id = ""
    if status == "ready" and voice_id and tts_usable is not False:
        # RunPod /v1/tts voice_id must be the profile UUID, not vp_* engine name.
        provider_voice_id = voice_id

    return {
        "runpod_voice_uuid": voice_id,
        "provider_voice_id": provider_voice_id,
        "engine_voice_name": engine_voice,
        "status": status,
        "status_message": status_message,
        "tts_usable": tts_usable,
        "raw": body,
    }


def fetch_voice_capabilities() -> dict:
    """GET {VOICE_PROFILE_TTS_BASE_URL}/v1/voices/capabilities"""
    base = _base_url()
    if not base:
        log_block(
            logger,
            logging.ERROR,
            operation="VOICE_PROFILE_CAPABILITIES_REQUEST",
            step="config",
            status="FAIL",
            error="VOICE_PROFILE_TTS_BASE_URL is not configured",
        )
        return {
            "ok": False,
            "error": "VOICE_PROFILE_TTS_BASE_URL is not configured",
        }

    url = f"{base}/v1/voices/capabilities"
    log_block(
        logger,
        logging.INFO,
        operation="VOICE_PROFILE_CAPABILITIES_REQUEST",
        step="http_get",
        status="START",
        url=url,
        tts_base_url=base,
    )
    try:
        with httpx.Client(timeout=_tts_timeout_s()) as client:
            resp = client.get(url)
        if resp.status_code >= 400:
            log_block(
                logger,
                logging.ERROR,
                operation="VOICE_PROFILE_CAPABILITIES_RESPONSE",
                step="http_get",
                status="FAIL",
                url=url,
                http_status=resp.status_code,
                body_preview=resp.text[:300],
            )
            return {
                "ok": False,
                "error": f"HTTP {resp.status_code}: {resp.text[:300]}",
            }
        data = resp.json()
        log_block(
            logger,
            logging.INFO,
            operation="VOICE_PROFILE_CAPABILITIES_RESPONSE",
            step="http_get",
            status="OK",
            url=url,
            supports_reference_audio_cloning=data.get("supports_reference_audio_cloning"),
            supports_voice_builder_json=data.get("supports_voice_builder_json"),
            clone_endpoint=data.get("clone_endpoint"),
        )
        return {"ok": True, **data}
    except Exception as exc:
        log_block(
            logger,
            logging.ERROR,
            operation="VOICE_PROFILE_CAPABILITIES_RESPONSE",
            step="http_get",
            status="FAIL",
            url=url,
            error=str(exc)[:400],
        )
        return {"ok": False, "error": str(exc)}


def clone_voice_on_runpod(
    *,
    audio_path: Path,
    display_name: str,
    filename: str,
    content_type: str = "application/octet-stream",
) -> dict:
    """
    Clone voice on RunPod.

    POST {TTS_BASE_URL}/v1/voices/clone
      multipart: file, name, consent_confirmed
    """
    base = _base_url()
    if not base:
        raise ValueError("VOICE_PROFILE_TTS_BASE_URL is not configured")

    url = f"{base}{_clone_endpoint()}"
    file_size = audio_path.stat().st_size
    log_block(
        logger,
        logging.INFO,
        operation="VOICE_PROFILE_UPLOAD_START",
        step="audio_clone",
        status="START",
        url=url,
        display_name=display_name,
        filename=filename,
        file_size=file_size,
    )
    try:
        with audio_path.open("rb") as audio_file:
            files = {"file": (filename, audio_file, content_type)}
            data = {
                "name": display_name,
                "display_name": display_name,
                "consent_confirmed": "true",
            }
            with httpx.Client(timeout=_clone_timeout_s()) as client:
                resp = client.post(url, files=files, data=data)
    except Exception as exc:
        log_block(
            logger,
            logging.ERROR,
            operation="VOICE_PROFILE_UPLOAD_FAILED",
            step="audio_clone",
            status="FAIL",
            url=url,
            error=str(exc)[:400],
        )
        raise

    try:
        body = resp.json()
    except Exception:
        body = {}

    if resp.status_code == 422 or resp.status_code >= 400:
        detail = _extract_error_detail(body, resp.text[:500])
        log_block(
            logger,
            logging.ERROR,
            operation="VOICE_PROFILE_UPLOAD_FAILED",
            step="audio_clone",
            status="FAIL",
            url=url,
            http_status=resp.status_code,
            error=detail[:400],
        )
        raise RuntimeError(f"RunPod voice clone failed (HTTP {resp.status_code}): {detail}")

    if body.get("error"):
        detail = _extract_error_detail(body)
        log_block(
            logger,
            logging.ERROR,
            operation="VOICE_PROFILE_UPLOAD_FAILED",
            step="audio_clone",
            status="FAIL",
            url=url,
            error=detail[:400],
        )
        raise RuntimeError(f"RunPod voice clone failed: {detail}")

    parsed = parse_runpod_voice_response(body)
    log_block(
        logger,
        logging.INFO,
        operation="VOICE_PROFILE_UPLOAD_SUCCESS",
        step="audio_clone",
        status="OK",
        url=url,
        voice_id=parsed["runpod_voice_uuid"] or "-",
        provider_voice_id=parsed["provider_voice_id"] or "-",
        runpod_status=parsed["status"],
    )

    if parsed["status"] == "stored_only":
        msg = (
            parsed.get("status_message")
            or "Voice stored but not synthesizable (stored_only). "
            "Set SUPERTONE_API_KEY on the RunPod server for audio cloning."
        )
        raise RuntimeError(msg)

    if parsed["status"] == "failed":
        raise RuntimeError(parsed.get("status_message") or "RunPod voice clone failed")

    if parsed["status"] != "ready":
        if not parsed["runpod_voice_uuid"]:
            raise RuntimeError(
                parsed.get("status_message")
                or f"RunPod clone not ready (status={parsed['status'] or 'unknown'})"
            )

    return parsed


def upload_voice_builder_json_on_runpod(
    *,
    json_path: Path,
    display_name: str,
    filename: str,
) -> dict:
    """
    Upload Supertone Voice Builder JSON for local open-source Supertonic.

    POST {TTS_BASE_URL}/v1/voices
      multipart: file, display_name, consent_confirmed
    """
    base = _base_url()
    if not base:
        raise ValueError("VOICE_PROFILE_TTS_BASE_URL is not configured")

    url = f"{base}{_upload_endpoint()}"
    file_size = json_path.stat().st_size
    log_block(
        logger,
        logging.INFO,
        operation="VOICE_PROFILE_UPLOAD_START",
        step="voice_builder_json",
        status="START",
        url=url,
        display_name=display_name,
        filename=filename,
        file_size=file_size,
    )
    try:
        with json_path.open("rb") as json_file:
            files = {"file": (filename, json_file, "application/json")}
            data = {
                "display_name": display_name,
                "consent_confirmed": "true",
            }
            with httpx.Client(timeout=_clone_timeout_s()) as client:
                resp = client.post(url, files=files, data=data)
    except Exception as exc:
        log_block(
            logger,
            logging.ERROR,
            operation="VOICE_PROFILE_UPLOAD_FAILED",
            step="voice_builder_json",
            status="FAIL",
            url=url,
            error=str(exc)[:400],
        )
        raise

    try:
        body = resp.json()
    except Exception:
        body = {}

    if resp.status_code == 422 or resp.status_code >= 400:
        detail = _extract_error_detail(body, resp.text[:500])
        log_block(
            logger,
            logging.ERROR,
            operation="VOICE_PROFILE_UPLOAD_FAILED",
            step="voice_builder_json",
            status="FAIL",
            url=url,
            http_status=resp.status_code,
            error=detail[:400],
        )
        raise RuntimeError(
            f"RunPod voice JSON upload failed (HTTP {resp.status_code}): {detail}"
        )

    if body.get("error"):
        detail = _extract_error_detail(body)
        log_block(
            logger,
            logging.ERROR,
            operation="VOICE_PROFILE_UPLOAD_FAILED",
            step="voice_builder_json",
            status="FAIL",
            url=url,
            error=detail[:400],
        )
        raise RuntimeError(f"RunPod voice JSON upload failed: {detail}")

    parsed = parse_runpod_voice_response(body)
    log_block(
        logger,
        logging.INFO,
        operation="VOICE_PROFILE_UPLOAD_SUCCESS",
        step="voice_builder_json",
        status="OK",
        url=url,
        voice_id=parsed["runpod_voice_uuid"] or "-",
        provider_voice_id=parsed["provider_voice_id"] or "-",
        runpod_status=parsed["status"],
    )

    if parsed["status"] == "stored_only":
        raise RuntimeError(
            parsed.get("status_message")
            or "Voice JSON stored but not synthesizable (stored_only)."
        )

    if parsed["status"] == "failed":
        raise RuntimeError(parsed.get("status_message") or "RunPod voice JSON upload failed")

    if parsed["status"] != "ready" or not parsed["provider_voice_id"]:
        raise RuntimeError(
            parsed.get("status_message")
            or f"RunPod voice JSON not ready (status={parsed['status'] or 'unknown'})"
        )

    return parsed


def synthesize_voice_test_on_runpod(
    *,
    provider_voice_id: str,
    text: str = "Hello, this is a voice test.",
) -> bytes:
    """POST {TTS_BASE_URL}/v1/tts for a short voice preview."""
    base = _base_url()
    if not base:
        raise ValueError("TTS_BASE_URL is not configured")
    if not provider_voice_id.strip():
        raise ValueError("provider_voice_id is required")

    url = f"{base}{_synth_endpoint_path()}"
    payload = {
        "text": text,
        "voice_id": provider_voice_id.strip(),
        "lang": _tts_lang(),
        "response_format": "wav",
        "model": _tts_model(),
    }
    logger.info(
        "runpod_voice_test_tts_request url=%s voice_id=%s text_chars=%s",
        url,
        provider_voice_id,
        len(text),
    )
    with httpx.Client(timeout=_tts_timeout_s()) as client:
        resp = client.post(url, json=payload)

    logger.info(
        "runpod_voice_test_tts_response http=%s bytes=%s",
        resp.status_code,
        len(resp.content),
    )

    if resp.status_code == 404:
        raise RuntimeError(
            f"RunPod voice not found (HTTP 404). voice_id={provider_voice_id!r}"
        )

    if resp.status_code >= 400:
        detail = resp.text[:300]
        try:
            detail = _extract_error_detail(resp.json(), detail)
        except Exception:
            pass
        raise RuntimeError(f"RunPod TTS test failed (HTTP {resp.status_code}): {detail}")

    if not resp.content:
        raise RuntimeError("RunPod TTS test returned empty audio")

    return resp.content


def fetch_voice_status_on_runpod(*, runpod_voice_uuid: str) -> dict:
    """GET {TTS_BASE_URL}/v1/voices/{voice_id}"""
    base = _base_url()
    if not base or not runpod_voice_uuid:
        raise ValueError("TTS_BASE_URL or runpod_voice_uuid missing")

    url = f"{base}/v1/voices/{runpod_voice_uuid}"
    with httpx.Client(timeout=_tts_timeout_s()) as client:
        resp = client.get(url)

    logger.info(
        "runpod_voice_status_response uuid=%s http=%s bytes=%s",
        runpod_voice_uuid,
        resp.status_code,
        len(resp.content),
    )

    if resp.status_code >= 400:
        detail = resp.text[:300]
        try:
            detail = _extract_error_detail(resp.json(), detail)
        except Exception:
            pass
        raise RuntimeError(
            f"RunPod voice status failed (HTTP {resp.status_code}): {detail}"
        )

    parsed = parse_runpod_voice_response(resp.json())
    logger.info(
        "runpod_voice_status_parsed uuid=%s provider_voice_id=%s status=%s",
        parsed["runpod_voice_uuid"],
        parsed["provider_voice_id"] or "-",
        parsed["status"],
    )
    return parsed


def poll_voice_until_ready(
    runpod_voice_uuid: str,
    *,
    timeout_s: float | None = None,
    interval_s: float | None = None,
) -> dict:
    """Poll RunPod until status=ready and voice_id is usable for TTS."""
    timeout = timeout_s if timeout_s is not None else _poll_timeout_s()
    interval = interval_s if interval_s is not None else _poll_interval_s()
    deadline = time.monotonic() + timeout
    last: dict | None = None

    while time.monotonic() < deadline:
        last = fetch_voice_status_on_runpod(runpod_voice_uuid=runpod_voice_uuid)
        status = last.get("status", "")

        if status == "failed":
            raise RuntimeError(last.get("status_message") or "RunPod voice cloning failed")

        if status == "stored_only":
            raise RuntimeError(
                last.get("status_message")
                or "Voice stored but not synthesizable (stored_only)."
            )

        if status == "ready" and (last.get("provider_voice_id") or "").strip():
            return last

        time.sleep(interval)

    if last and last.get("status") == "stored_only":
        raise RuntimeError(
            last.get("status_message") or "Voice stored but not synthesizable (stored_only)."
        )

    msg = "RunPod voice cloning timed out while processing"
    if last and last.get("status_message"):
        msg = f"{msg}: {last['status_message']}"
    raise RuntimeError(msg)


def delete_voice_on_runpod(*, runpod_voice_uuid: str) -> None:
    """DELETE {TTS_BASE_URL}/v1/voices/{voice_id}"""
    base = _base_url()
    if not base or not runpod_voice_uuid:
        return

    url = f"{base}/v1/voices/{runpod_voice_uuid}"
    logger.info("runpod_voice_delete_request url=%s", url)
    with httpx.Client(timeout=_tts_timeout_s()) as client:
        resp = client.delete(url)
    logger.info("runpod_voice_delete_response status=%s", resp.status_code)
    if resp.status_code >= 400 and resp.status_code != 404:
        detail = resp.text[:300]
        raise RuntimeError(
            f"RunPod voice delete failed (HTTP {resp.status_code}): {detail}"
        )
