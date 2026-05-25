"""Select STT backend from VOICE_AGENT_STT_PROVIDER (deepgram only today)."""

from __future__ import annotations

import os

from livekit.plugins import deepgram


def get_stt_provider() -> str:
    return (os.environ.get("VOICE_AGENT_STT_PROVIDER") or "deepgram").strip().lower()


def build_stt(language: str = "en") -> deepgram.STT:
    if get_stt_provider() == "azure":
        raise NotImplementedError(
            "Azure STT is not wired in this repo; set VOICE_AGENT_STT_PROVIDER=deepgram"
        )
    from agent.pipeline.stt_deepgram import build_deepgram_stt

    return build_deepgram_stt(language)
