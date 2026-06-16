"""Resolve effective TTS voice config for calls and the LiveKit worker."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.pipeline.voice_presets import resolve_tts_config

if TYPE_CHECKING:
    from apps.calls.models import CallSession, VoiceProfile


def effective_tts_voice(cfg: dict) -> str:
    """Voice string sent to RunPod POST /v1/tts."""
    provider = (cfg.get("provider_voice_id") or "").strip()
    if provider:
        return provider
    return str(
        cfg.get("fallback_voice")
        or cfg.get("voice")
        or cfg.get("predefined_voice_id")
        or "M1"
    )


def resolve_fallback_voice(persona_id: str = "", *, language: str = "en") -> str:
    from django.conf import settings

    cfg = resolve_tts_config(persona_id, language=language)
    return str(
        cfg.get("voice")
        or cfg.get("predefined_voice_id")
        or getattr(settings, "TTS_VOICE", "M1")
        or "M1"
    )


def build_tts_metadata_for_call(
    *,
    persona_id: str = "",
    language: str = "en",
    voice_mode: str = "preset",
    voice_profile: VoiceProfile | None = None,
    fallback_voice: str = "",
) -> dict[str, str]:
    """Build TTS + voice metadata for CallSession and LiveKit dispatch."""
    fb = fallback_voice or resolve_fallback_voice(persona_id, language=language)
    preset = resolve_tts_config(persona_id, language=language)
    cfg: dict[str, str] = {
        "voice_mode": voice_mode if voice_mode in ("preset", "custom") else "preset",
        "lang": language,
        "voice": preset.get("voice") or fb,
        "fallback_voice": fb,
    }

    if voice_mode == "custom" and voice_profile:
        provider_id = (voice_profile.provider_voice_id or "").strip()
        if not provider_id:
            raise ValueError(
                f"Voice profile {voice_profile.id} has no provider_voice_id"
            )
        cfg["voice_mode"] = "custom"
        cfg["voice_profile_id"] = str(voice_profile.id)
        cfg["provider_voice_id"] = provider_id
        cfg["voice_id"] = provider_id
        if voice_profile.runpod_voice_uuid:
            cfg["runpod_voice_uuid"] = voice_profile.runpod_voice_uuid

    return cfg


def merge_tts_config_from_sources(
    *,
    language: str,
    persona_id: str = "",
    meta: dict | None = None,
    db_session: CallSession | None = None,
) -> dict[str, str]:
    """
    Worker-side resolution: custom cloned voice wins over preset persona.
    """
    meta = meta or {}
    fb = resolve_fallback_voice(persona_id, language=language)
    voice_mode = "preset"
    provider_voice_id = ""
    voice_profile_id = ""

    if db_session:
        fb = (db_session.fallback_voice or "").strip() or fb
        persona_id = db_session.persona_id or persona_id
        if (db_session.provider_voice_id or "").strip():
            provider_voice_id = db_session.provider_voice_id.strip()
            voice_mode = "custom"
        if db_session.voice_profile_id:
            voice_profile_id = str(db_session.voice_profile_id)
            vp = getattr(db_session, "voice_profile", None)
            if vp and (vp.provider_voice_id or "").strip():
                provider_voice_id = vp.provider_voice_id.strip()
                voice_mode = "custom"

    if meta.get("voice_mode") in ("preset", "custom"):
        voice_mode = str(meta["voice_mode"])
    if (meta.get("provider_voice_id") or meta.get("voice_id") or "").strip():
        provider_voice_id = str(meta.get("provider_voice_id") or meta.get("voice_id")).strip()
        voice_mode = "custom"
    if meta.get("voice_profile_id"):
        voice_profile_id = str(meta["voice_profile_id"])
    if (meta.get("fallback_voice") or "").strip():
        fb = str(meta["fallback_voice"]).strip()

    tts_meta = meta.get("tts")
    if isinstance(tts_meta, dict):
        if (tts_meta.get("provider_voice_id") or "").strip():
            provider_voice_id = str(tts_meta["provider_voice_id"]).strip()
            voice_mode = "custom"
        if tts_meta.get("voice_profile_id"):
            voice_profile_id = str(tts_meta["voice_profile_id"])
        if (tts_meta.get("fallback_voice") or "").strip():
            fb = str(tts_meta["fallback_voice"]).strip()
        if tts_meta.get("voice_mode") in ("preset", "custom"):
            voice_mode = str(tts_meta["voice_mode"])

    preset = resolve_tts_config(persona_id, language=language)
    cfg: dict[str, str] = {
        "voice_mode": voice_mode,
        "lang": language,
        "voice": preset.get("voice") or fb,
        "fallback_voice": fb,
    }
    if provider_voice_id:
        cfg["provider_voice_id"] = provider_voice_id
        cfg["voice_id"] = provider_voice_id
        cfg["voice_mode"] = "custom"
    if voice_profile_id:
        cfg["voice_profile_id"] = voice_profile_id
    if meta.get("tts_base_url"):
        cfg["tts_base_url"] = str(meta["tts_base_url"])

    return cfg
