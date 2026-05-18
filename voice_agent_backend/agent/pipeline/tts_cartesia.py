"""Cartesia TTS configuration for LiveKit Agents plugin."""

from __future__ import annotations

import os

from livekit.plugins import cartesia


def build_cartesia_tts() -> cartesia.TTS:
    return cartesia.TTS(
        api_key=os.environ.get("CARTESIA_API_KEY"),
        model="sonic-3",
        voice=os.environ.get(
            "VOICE_AGENT_CARTESIA_VOICE_ID",
            "0ad65e7f-006c-47cf-bd31-52279d487913",
        ),
        sample_rate=24000,
        encoding="pcm_s16le",
    )
