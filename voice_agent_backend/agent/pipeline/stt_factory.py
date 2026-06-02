"""Select STT backend from STT_PROVIDER (default: faster_whisper; deepgram is opt-in)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent.pipeline.stt_config import (
    get_stt_provider,
    is_deepgram_provider,
    is_faster_whisper_provider,
    stt_provider_label,
)

if TYPE_CHECKING:
    import aiohttp

    from livekit.agents import stt as lk_stt


def build_stt(
    language: str = "en",
    *,
    http_session: aiohttp.ClientSession | None = None,
) -> lk_stt.STT:
    provider = get_stt_provider()
    if is_faster_whisper_provider(provider):
        from agent.pipeline.stt_faster_whisper import build_faster_whisper_stt

        if http_session is None:
            raise ValueError("http_session is required for Faster Whisper STT")
        return build_faster_whisper_stt(language, http_session=http_session)

    if is_deepgram_provider(provider):
        from agent.pipeline.stt_deepgram import build_deepgram_stt

        return build_deepgram_stt(language, http_session=http_session)

    raise ValueError(
        f"Unknown STT_PROVIDER={provider!r}; use faster_whisper (aliases: wlk, whisperlivekit) "
        "or deepgram"
    )


def resolve_stt_log_fields(language: str = "en") -> dict[str, str]:
    """Model/language fields for pipeline init logs."""
    if is_faster_whisper_provider():
        from agent.pipeline.stt_config import resolve_faster_whisper_language

        return {
            "stt_provider": stt_provider_label(),
            "model": "faster-whisper",
            "stt_language": resolve_faster_whisper_language(language),
        }
    from agent.pipeline.stt_deepgram import resolve_deepgram_stt

    model, dg_lang = resolve_deepgram_stt(language)
    return {
        "stt_provider": "deepgram",
        "model": model,
        "stt_language": dg_lang,
    }
