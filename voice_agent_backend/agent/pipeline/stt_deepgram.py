"""Deepgram STT — user speech locale is routed separately from agent/TTS language."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from agent.pipeline.user_stt_router import default_user_stt_language
from agent.prompts import normalize_language
from livekit.plugins import deepgram

if TYPE_CHECKING:
    import aiohttp

# Nova-3: https://developers.deepgram.com/docs/models-languages-overview
_DEEPGRAM_STT_BY_CONVERSATION_LANG: dict[str, dict[str, str]] = {
    "en": {"model": "nova-3", "language": "en-US"},
    # Initial STT for ar calls is en-US; entrypoint switches to ar-SA when Arabic is heard.
    "ar": {"model": "nova-3", "language": "en-US"},
}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def resolve_deepgram_stt(conversation_language: str = "en") -> tuple[str, str]:
    """Return (model, deepgram_language) for transcribing caller speech (UI transcript)."""
    app_lang = normalize_language(conversation_language)
    cfg = _DEEPGRAM_STT_BY_CONVERSATION_LANG.get(
        app_lang, _DEEPGRAM_STT_BY_CONVERSATION_LANG["en"]
    )
    model = (os.environ.get("VOICE_AGENT_DEEPGRAM_MODEL") or cfg["model"]).strip()
    if os.environ.get("VOICE_AGENT_DEEPGRAM_LANGUAGE"):
        dg_language = os.environ["VOICE_AGENT_DEEPGRAM_LANGUAGE"].strip()
    else:
        dg_language = default_user_stt_language(conversation_language)
    return model, dg_language


def build_deepgram_stt(
    conversation_language: str = "en",
    *,
    http_session: aiohttp.ClientSession | None = None,
) -> deepgram.STT:
    model, dg_language = resolve_deepgram_stt(conversation_language)
    # Deepgram streaming endpointing (ms of silence before a final transcript).
    # Same role as vad_turnoff_ms in raw Deepgram API; LiveKit plugin uses endpointing_ms.
    endpointing_ms = _env_int("VOICE_AGENT_DEEPGRAM_ENDPOINTING_MS", 300)
    return deepgram.STT(
        model=model,
        language=dg_language,
        api_key=os.environ.get("DEEPGRAM_API_KEY"),
        interim_results=True,
        punctuate=True,
        smart_format=True,
        no_delay=True,
        sample_rate=_env_int("VOICE_AGENT_STT_SAMPLE_RATE", 16000),
        endpointing_ms=endpointing_ms,
        vad_events=True,
        filler_words=_env_bool("VOICE_AGENT_DEEPGRAM_FILLER_WORDS", True),
        http_session=http_session,
    )
