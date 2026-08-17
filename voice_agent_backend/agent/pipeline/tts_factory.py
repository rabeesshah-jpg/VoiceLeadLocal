"""Select TTS backend from TTS_PROVIDER (multilingual | supertonic | cartesia)."""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import TYPE_CHECKING

from agent.observability.latency import TtsCallTiming
from agent.observability.pipeline_latency import TurnPipelineTracker

if TYPE_CHECKING:
    from livekit.agents import tts as lk_tts


def get_tts_provider() -> str:
    return (os.environ.get("TTS_PROVIDER") or "multilingual").strip().lower()


def build_tts(
    overrides: dict[str, str | float] | None = None,
    *,
    on_timing: Callable[[TtsCallTiming], None] | None = None,
    pipeline_tracker: TurnPipelineTracker | None = None,
    on_llm_first_token: Callable[[], None] | None = None,
) -> lk_tts.TTS:
    provider = get_tts_provider()

    if provider == "supertonic":
        from agent.pipeline.tts_supertonic import build_supertonic_tts

        return build_supertonic_tts(
            overrides,
            on_timing=on_timing,
            pipeline_tracker=pipeline_tracker,
            on_llm_first_token=on_llm_first_token,
        )

    if provider == "cartesia":
        from livekit.plugins import cartesia

        # Presets (VOICE_PRESETS) hold multilingual-server voice tags like
        # "male"/"female", not Cartesia voice IDs, so overrides are ignored
        # here on purpose. Always use the real Cartesia voice UUID from env.
        voice = os.environ.get("CARTESIA_VOICE_ID", "<voice_id>")
        language = (overrides or {}).get("language") or os.environ.get(
            "CARTESIA_LANGUAGE", "en"
        )
        return cartesia.TTS(
            model="sonic-2",
            voice=voice,
            language=language,
        )

    from agent.pipeline.tts_multilingual_server import build_multilingual_tts

    return build_multilingual_tts(
        overrides,
        on_timing=on_timing,
        pipeline_tracker=pipeline_tracker,
        on_llm_first_token=on_llm_first_token,
    )