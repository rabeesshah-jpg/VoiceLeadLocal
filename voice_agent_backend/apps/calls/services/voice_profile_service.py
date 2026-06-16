"""Voice profile CRUD and RunPod clone orchestration."""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from django.conf import settings
from django.core.files.base import ContentFile
from apps.calls.models import VoiceProfile
from apps.calls.services.tts_voice_clone import (
    clone_voice_on_runpod,
    delete_voice_on_runpod,
    fetch_voice_capabilities,
    fetch_voice_status_on_runpod,
    poll_voice_until_ready,
    synthesize_voice_test_on_runpod,
    upload_voice_builder_json_on_runpod,
)
from apps.calls.services.voice_tts_config import (
    build_tts_metadata_for_call,
    resolve_fallback_voice,
)

logger = logging.getLogger("apps.calls.services.voice_profile")

__all__ = [
    "build_tts_metadata_for_call",
    "resolve_fallback_voice",
    "get_voice_clone_capabilities",
    "create_cloned_voice_profile",
    "create_voice_builder_json_profile",
    "delete_voice_profile",
    "refresh_voice_profile_from_runpod",
    "set_default_voice_profile",
    "test_voice_profile_tts",
]


def _allowed_extension(filename: str) -> bool:
    ext = Path(filename).suffix.lstrip(".").lower()
    allowed = getattr(settings, "TTS_CLONE_ALLOWED_EXTENSIONS", ["wav", "webm", "mp3", "m4a"])
    return ext in allowed


def _is_json_file(filename: str) -> bool:
    return Path(filename).suffix.lstrip(".").lower() == "json"


def _consent_confirmed(value) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return False


def _guess_content_type(filename: str) -> str:
    guessed, _ = mimetypes.guess_type(filename)
    return guessed or "application/octet-stream"


def get_voice_clone_capabilities() -> dict:
    caps = fetch_voice_capabilities()
    enabled = getattr(settings, "TTS_VOICE_CLONING_ENABLED", True)
    if not enabled:
        return {
            "voice_cloning_enabled": False,
            "supports_reference_audio_cloning": False,
            "message": "Voice cloning is disabled in server config.",
        }
    if not caps.get("ok"):
        err = caps.get("error", "Could not reach RunPod TTS capabilities.")
        hint = (
            " Set VOICE_PROFILE_TTS_BASE_URL in voice_agent_backend/.env to your "
            "RunPod TTS public URL (e.g. http://<ip>:<port>)."
        )
        if "not configured" in str(err).lower():
            err = f"{err}.{hint}"
        return {
            "voice_cloning_enabled": False,
            "supports_reference_audio_cloning": False,
            "message": err,
        }
    supports_audio = bool(caps.get("supports_reference_audio_cloning"))
    supports_json = bool(caps.get("supports_voice_builder_json", True))
    message = caps.get("notes") or ""
    if not supports_audio:
        message = (
            "Recording-based cloning is not enabled on this server. "
            "Upload a Voice Builder JSON file instead."
        )
    return {
        "voice_cloning_enabled": True,
        "supports_reference_audio_cloning": supports_audio,
        "supports_voice_builder_json": supports_json,
        "clone_endpoint": caps.get("clone_endpoint", "/v1/voices/clone"),
        "upload_endpoint": caps.get("upload_endpoint", "/v1/voices"),
        "tts_endpoint": caps.get("tts_endpoint", "/v1/tts"),
        "message": message,
    }


def _apply_runpod_result(profile: VoiceProfile, result: dict) -> VoiceProfile:
    """Map RunPod clone/status response onto VoiceProfile."""
    runpod_status = str(result.get("status") or "")
    voice_id = (result.get("runpod_voice_uuid") or "").strip()
    provider_id = (result.get("provider_voice_id") or "").strip()
    status_message = (result.get("status_message") or "").strip()

    profile.runpod_voice_uuid = voice_id or profile.runpod_voice_uuid

    if runpod_status == "failed":
        profile.status = VoiceProfile.STATUS_FAILED
        profile.provider_voice_id = ""
        profile.error_message = (status_message or "RunPod reported voice clone failed")[:1000]
    elif runpod_status == "stored_only":
        profile.status = VoiceProfile.STATUS_FAILED
        profile.provider_voice_id = ""
        profile.error_message = (
            status_message
            or "Voice stored but not synthesizable (stored_only). "
            "Set SUPERTONE_API_KEY on RunPod for audio cloning."
        )[:1000]
    elif runpod_status == "ready" and provider_id:
        profile.status = VoiceProfile.STATUS_READY
        profile.provider_voice_id = provider_id
        profile.error_message = ""
    elif runpod_status == "processing" and voice_id:
        profile.status = VoiceProfile.STATUS_PROCESSING
        profile.provider_voice_id = ""
        profile.error_message = ""
    else:
        profile.status = VoiceProfile.STATUS_FAILED
        profile.provider_voice_id = ""
        profile.error_message = (
            status_message
            or f"RunPod clone not ready (status={runpod_status or 'unknown'})"
        )[:1000]

    profile.save(
        update_fields=[
            "status",
            "provider_voice_id",
            "runpod_voice_uuid",
            "error_message",
            "updated_at",
        ]
    )
    logger.info(
        "voice_profile_applied id=%s status=%s provider_voice_id=%s runpod_uuid=%s",
        profile.id,
        profile.status,
        profile.provider_voice_id or "-",
        profile.runpod_voice_uuid or "-",
    )
    return profile


def refresh_voice_profile_from_runpod(
    profile: VoiceProfile, *, wait: bool = False
) -> VoiceProfile:
    if not profile.runpod_voice_uuid:
        profile.status = VoiceProfile.STATUS_FAILED
        profile.error_message = "Missing RunPod voice_id — cannot refresh status"
        profile.save(update_fields=["status", "error_message", "updated_at"])
        return profile

    if profile.status == VoiceProfile.STATUS_READY and profile.provider_voice_id:
        return profile

    try:
        if wait:
            result = poll_voice_until_ready(profile.runpod_voice_uuid)
            return _apply_runpod_result(profile, result)
        result = fetch_voice_status_on_runpod(runpod_voice_uuid=profile.runpod_voice_uuid)
    except Exception as exc:
        profile.error_message = str(exc)[:1000]
        if "stored_only" in str(exc).lower() or "superton" in str(exc).lower():
            profile.status = VoiceProfile.STATUS_FAILED
            profile.save(update_fields=["status", "error_message", "updated_at"])
        else:
            profile.save(update_fields=["error_message", "updated_at"])
        raise

    return _apply_runpod_result(profile, result)


def create_cloned_voice_profile(
    *,
    name: str,
    audio_bytes: bytes,
    filename: str,
) -> VoiceProfile:
    caps = get_voice_clone_capabilities()
    if not caps.get("voice_cloning_enabled"):
        raise ValueError(caps.get("message") or "Voice cloning is disabled")
    if not caps.get("supports_reference_audio_cloning"):
        raise ValueError(
            "Recording-based voice cloning is not enabled on this TTS server. "
            "Upload a Voice Builder JSON file instead."
        )

    if not _allowed_extension(filename):
        allowed = ", ".join(getattr(settings, "TTS_CLONE_ALLOWED_EXTENSIONS", []))
        raise ValueError(f"Unsupported audio format. Allowed: {allowed}")

    max_bytes = int(getattr(settings, "TTS_CLONE_MAX_BYTES", 15 * 1024 * 1024))
    if len(audio_bytes) > max_bytes:
        raise ValueError(f"Audio file too large (max {max_bytes // (1024 * 1024)} MB)")

    if not name.strip():
        raise ValueError("Voice profile name is required")

    profile = VoiceProfile.objects.create(
        name=name.strip(),
        status=VoiceProfile.STATUS_PROCESSING,
        provider=VoiceProfile.PROVIDER_RUNPOD_SUPERTONIC,
        source_type=VoiceProfile.SOURCE_REFERENCE_AUDIO,
    )
    profile.reference_audio_file.save(filename, ContentFile(audio_bytes), save=True)

    try:
        audio_path = Path(profile.reference_audio_file.path)
        result = clone_voice_on_runpod(
            audio_path=audio_path,
            display_name=profile.name,
            filename=filename,
            content_type=_guess_content_type(filename),
        )
        profile = _apply_runpod_result(profile, result)

        if profile.status == VoiceProfile.STATUS_PROCESSING and profile.runpod_voice_uuid:
            polled = poll_voice_until_ready(profile.runpod_voice_uuid)
            profile = _apply_runpod_result(profile, polled)

        if profile.status != VoiceProfile.STATUS_READY or not profile.provider_voice_id:
            raise RuntimeError(
                profile.error_message
                or "RunPod did not return a ready voice_id for synthesis"
            )

        logger.info(
            "voice_profile_clone_ready id=%s name=%r provider_voice_id=%s runpod_uuid=%s",
            profile.id,
            profile.name,
            profile.provider_voice_id,
            profile.runpod_voice_uuid,
        )
        return profile
    except Exception as exc:
        profile.status = VoiceProfile.STATUS_FAILED
        profile.error_message = str(exc)[:1000]
        profile.save(update_fields=["status", "error_message", "updated_at"])
        logger.exception("voice_profile_clone_failed id=%s", profile.id)
        raise


def create_voice_builder_json_profile(
    *,
    name: str,
    json_bytes: bytes,
    filename: str,
    consent_confirmed: bool,
) -> VoiceProfile:
    caps = get_voice_clone_capabilities()
    if not caps.get("voice_cloning_enabled"):
        raise ValueError(caps.get("message") or "Voice cloning is disabled")
    if not caps.get("supports_voice_builder_json"):
        raise ValueError(
            "Voice Builder JSON upload is not supported on this RunPod TTS server."
        )
    if not _consent_confirmed(consent_confirmed):
        raise ValueError("consent_confirmed must be true")
    if not _is_json_file(filename):
        raise ValueError("Only .json Voice Builder files are supported")
    if not name.strip():
        raise ValueError("Voice profile name is required")

    max_bytes = int(getattr(settings, "TTS_CLONE_MAX_BYTES", 15 * 1024 * 1024))
    if len(json_bytes) > max_bytes:
        raise ValueError(f"JSON file too large (max {max_bytes // (1024 * 1024)} MB)")

    profile = VoiceProfile.objects.create(
        name=name.strip(),
        status=VoiceProfile.STATUS_PROCESSING,
        provider=VoiceProfile.PROVIDER_RUNPOD_SUPERTONIC,
        source_type=VoiceProfile.SOURCE_VOICE_BUILDER_JSON,
    )
    profile.reference_audio_file.save(filename, ContentFile(json_bytes), save=True)

    try:
        json_path = Path(profile.reference_audio_file.path)
        result = upload_voice_builder_json_on_runpod(
            json_path=json_path,
            display_name=profile.name,
            filename=filename,
        )
        profile = _apply_runpod_result(profile, result)
        if profile.status != VoiceProfile.STATUS_READY or not profile.provider_voice_id:
            raise RuntimeError(
                profile.error_message
                or "RunPod did not return a ready voice_id for synthesis"
            )
        logger.info(
            "voice_profile_json_ready id=%s name=%r provider_voice_id=%s runpod_uuid=%s",
            profile.id,
            profile.name,
            profile.provider_voice_id,
            profile.runpod_voice_uuid,
        )
        return profile
    except Exception as exc:
        profile.status = VoiceProfile.STATUS_FAILED
        profile.error_message = str(exc)[:1000]
        profile.save(update_fields=["status", "error_message", "updated_at"])
        logger.exception("voice_profile_json_upload_failed id=%s", profile.id)
        raise


def test_voice_profile_tts(profile: VoiceProfile) -> bytes:
    if profile.status != VoiceProfile.STATUS_READY:
        raise ValueError(f"Voice profile is not ready (status: {profile.status})")
    if not profile.provider_voice_id:
        raise ValueError("Voice profile is missing provider_voice_id")

    try:
        return synthesize_voice_test_on_runpod(provider_voice_id=profile.provider_voice_id)
    except RuntimeError as exc:
        if "404" in str(exc) or "not found" in str(exc).lower():
            profile.status = VoiceProfile.STATUS_FAILED
            profile.error_message = str(exc)[:1000]
            profile.save(update_fields=["status", "error_message", "updated_at"])
        raise


def delete_voice_profile(profile: VoiceProfile) -> None:
    runpod_uuid = profile.runpod_voice_uuid
    if runpod_uuid:
        try:
            delete_voice_on_runpod(runpod_voice_uuid=runpod_uuid)
        except Exception as exc:
            logger.warning(
                "runpod_voice_delete_skipped id=%s error=%s",
                profile.id,
                str(exc)[:200],
            )
    if profile.reference_audio_file:
        profile.reference_audio_file.delete(save=False)
    profile.delete()


def set_default_voice_profile(profile: VoiceProfile) -> VoiceProfile:
    if not profile.provider_voice_id or profile.status != VoiceProfile.STATUS_READY:
        raise ValueError("Only ready profiles with provider_voice_id can be default")
    VoiceProfile.objects.filter(is_default=True).update(is_default=False)
    profile.is_default = True
    profile.save(update_fields=["is_default", "updated_at"])
    return profile
