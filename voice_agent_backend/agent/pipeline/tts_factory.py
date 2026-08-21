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
        from agent.pipeline.tts_cartesia_instrumented import InstrumentedCartesiaTTS

        # NOTE: config dict key is "lang" (see merge_tts_config_from_sources /
        # entrypoint.py's tts_config.get("lang")), not "language" — this was
        # previously reading the wrong key and silently always falling back
        # to CARTESIA_LANGUAGE (hardcoded "en"), regardless of the actual
        # per-call language.
        language = (overrides or {}).get("lang") or os.environ.get(
            "CARTESIA_LANGUAGE", "en"
        )
        # Presets (VOICE_PRESETS) hold multilingual-server voice tags like
        # "male"/"female", not Cartesia voice IDs, so overrides are ignored
        # for voice selection on purpose — always use a real Cartesia voice
        # UUID from env, chosen by language. Arabic calls use the Fatima
        # voice (CARTESIA_VOICE_ID_AR); everything else uses the default
        # English voice. Reads CARTESIA_VOICE_ID_EN first (matches the
        # env naming convention actually in use), falling back to the
        # older CARTESIA_VOICE_ID name for backward compatibility.
        if language == "ar":
            voice = os.environ.get("CARTESIA_VOICE_ID_AR") or os.environ.get(
                "CARTESIA_VOICE_ID", "<voice_id>"
            )
        else:
            voice = (
                os.environ.get("CARTESIA_VOICE_ID_EN")
                or os.environ.get("CARTESIA_VOICE_ID")
                or "<voice_id>"
            )
        # sonic-2's specific language list does not include Arabic;
        # Arabic support requires sonic-3 (or newer). Only use sonic-3 for
        # Arabic calls to avoid changing existing English call behavior.
        model = "sonic-3" if language == "ar" else "sonic-2"
        # InstrumentedCartesiaTTS is a drop-in subclass of the real
        # cartesia.TTS — identical synthesis behavior, adds real
        # tts_ttfb_ms/tts_total_ms timing (previously always None/missing,
        # since the plain cartesia.TTS has no timing hooks at all).
        return InstrumentedCartesiaTTS(
            model=model,
            voice=voice,
            language=language,
            pipeline_tracker=pipeline_tracker,
            on_timing=on_timing,
            on_llm_first_token=on_llm_first_token,
        )
    from agent.pipeline.tts_multilingual_server import build_multilingual_tts

    return build_multilingual_tts(
        overrides,
        on_timing=on_timing,
        pipeline_tracker=pipeline_tracker,
        on_llm_first_token=on_llm_first_token,
    )