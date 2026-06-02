"""STT provider selection and environment resolution."""

from __future__ import annotations

import os
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

_DEEPGRAM_ALIASES = frozenset({"deepgram"})
_DEFAULT_STT_PROVIDER = "faster_whisper"
_FASTER_WHISPER_ALIASES = frozenset(
    {
        "faster_whisper",
        "faster-whisper",
        "fasterwhisper",
        "wlk",
        "whisper",
        "whisperlivekit",
    }
)


def _env(name: str, legacy: str | None = None, default: str = "") -> str:
    val = os.environ.get(name)
    if val is not None and str(val).strip():
        return str(val).strip()
    if legacy:
        legacy_val = os.environ.get(legacy)
        if legacy_val is not None and str(legacy_val).strip():
            return str(legacy_val).strip()
    return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def get_stt_provider() -> str:
    """Active STT backend id (deepgram | faster_whisper | wlk, …)."""
    raw = ""
    try:
        from django.conf import settings

        if settings.configured:
            raw = (
                getattr(settings, "STT_PROVIDER", "")
                or getattr(settings, "VOICE_AGENT_STT_PROVIDER", "")
                or ""
            )
    except Exception:
        raw = ""
    if not raw:
        raw = _env("STT_PROVIDER", "VOICE_AGENT_STT_PROVIDER", "")
    return (raw or _DEFAULT_STT_PROVIDER).lower()


def is_deepgram_provider(provider: str | None = None) -> bool:
    p = (provider or get_stt_provider()).lower()
    return p in _DEEPGRAM_ALIASES or p == "deepgram"


def is_faster_whisper_provider(provider: str | None = None) -> bool:
    p = (provider or get_stt_provider()).lower()
    if p in _FASTER_WHISPER_ALIASES:
        return True
    return p.startswith("faster")


def stt_provider_label(provider: str | None = None) -> str:
    p = provider or get_stt_provider()
    if is_faster_whisper_provider(p):
        return p if p not in _FASTER_WHISPER_ALIASES else "faster_whisper"
    return "deepgram"


def faster_whisper_ws_mode() -> str:
    return _env("STT_WS_MODE", "FASTER_WHISPER_WS_MODE", "full") or "full"


def resolve_faster_whisper_ws_url(conversation_language: str = "en") -> str:
    """WebSocket URL for WhisperLiveKit / Faster Whisper ASR."""
    explicit = _env("STT_WS_URL")
    if explicit:
        base_url = explicit
    else:
        base = _env("FASTER_WHISPER_STT_URL", "STT_BASE_URL", "http://localhost:8000")
        if not base:
            base_url = "ws://localhost:8000/asr"
        else:
            parsed = urlparse(base)
            scheme = parsed.scheme.lower()
            if scheme in ("ws", "wss"):
                path = parsed.path or "/asr"
                if path in ("", "/"):
                    path = "/asr"
                base_url = urlunparse((scheme, parsed.netloc, path, "", "", ""))
            else:
                host = parsed.netloc or parsed.path
                path = parsed.path if parsed.netloc else "/asr"
                if path in ("", "/"):
                    path = "/asr"
                ws_scheme = "wss" if scheme == "https" else "ws"
                base_url = urlunparse((ws_scheme, host, path, "", "", ""))

    lang = resolve_faster_whisper_language(conversation_language)
    parsed = urlparse(base_url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("language", lang)
    query.setdefault("mode", faster_whisper_ws_mode())
    return urlunparse(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path or "/asr",
            parsed.params,
            urlencode(query),
            parsed.fragment,
        )
    )


def resolve_faster_whisper_language(conversation_language: str = "en") -> str:
    lang = _env("FASTER_WHISPER_LANGUAGE", "STT_LANGUAGE", "en")
    if lang.lower() == "auto":
        from agent.prompts import normalize_language

        return normalize_language(conversation_language)
    return lang


def faster_whisper_streaming_enabled() -> bool:
    return _env_bool("FASTER_WHISPER_STREAMING", True)


def faster_whisper_timeout_seconds() -> float:
    return float(_env_int("FASTER_WHISPER_TIMEOUT_SECONDS", 30))


def faster_whisper_sample_rate() -> int:
    return _env_int("VOICE_AGENT_STT_SAMPLE_RATE", 16000)


def faster_whisper_send_config() -> bool:
    # WhisperLiveKit sends config to the client; do not send a client config frame.
    return _env_bool("FASTER_WHISPER_SEND_CONFIG", False)


def _setting_or_env(name: str) -> str:
    try:
        from django.conf import settings

        if settings.configured:
            val = getattr(settings, name, "") or ""
            if str(val).strip():
                return str(val).strip()
    except Exception:
        pass
    return _env(name)


def validate_stt_env(provider: str | None = None) -> list[str]:
    """Return missing env var names for the active STT provider."""
    missing: list[str] = []
    if is_deepgram_provider(provider):
        if not _setting_or_env("DEEPGRAM_API_KEY"):
            missing.append("DEEPGRAM_API_KEY")
        return missing

    if is_faster_whisper_provider(provider):
        if not resolve_faster_whisper_ws_url("en"):
            missing.append("FASTER_WHISPER_STT_URL")
        return missing

    missing.append("STT_PROVIDER")
    return missing
