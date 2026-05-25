"""TTS voice presets (persona_id from start-call API)."""

from __future__ import annotations

# Multilingual server: speaker_wav is "male" | "female" (POST /tts_to_audio/).
# Supertonic (TTS_PROVIDER=supertonic): voice is M1–M5 / F1–F5.

VOICE_PRESETS: dict[str, dict[str, str]] = {
    "male": {
        "voice": "male",
        "speaker_wav": "male",
        "lang": "en",
    },
    "female": {
        "voice": "female",
        "speaker_wav": "female",
        "lang": "en",
    },
}

VOICE_PRESETS_SUPERTONIC: dict[str, dict[str, str]] = {
    "male": {"voice": "M1", "lang": "en"},
    "female": {"voice": "F1", "lang": "en"},
}

# --- Chatterbox multilingual (deprecated) ---
# VOICE_PRESETS_CHATTERBOX: dict[str, dict[str, str | float]] = {
#     "male": {
#         "predefined_voice_id": "Michael.wav",
#         "exaggeration": 0.65,
#         "temperature": 0.8,
#         "cfg_weight": 0.45,
#     },
#     "female": {
#         "predefined_voice_id": "Olivia.wav",
#         "exaggeration": 0.65,
#         "temperature": 0.8,
#         "cfg_weight": 0.45,
#     },
# }


def resolve_tts_config(
    persona_id: str | None,
    *,
    language: str = "en",
) -> dict[str, str]:
    import os

    from agent.prompts import normalize_language

    key = (persona_id or "").strip().lower()
    presets = (
        VOICE_PRESETS_SUPERTONIC
        if (os.environ.get("TTS_PROVIDER") or "multilingual").strip().lower() == "supertonic"
        else VOICE_PRESETS
    )
    cfg: dict[str, str] = dict(presets[key]) if key in presets else {}
    lang = normalize_language(language)
    cfg["lang"] = lang
    return cfg
