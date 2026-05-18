"""Deepgram STT configuration for LiveKit Agents plugin."""

from __future__ import annotations

import os

from livekit.plugins import deepgram


def build_deepgram_stt() -> deepgram.STT:
    return deepgram.STT(
        model="nova-2",
        api_key=os.environ.get("DEEPGRAM_API_KEY"),
        interim_results=True,
        punctuate=True,
        smart_format=True,
        sample_rate=16000,
        endpointing_ms=700,
        vad_events=True,
    )
