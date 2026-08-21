"""Fixed call-opening greeting (spoken via TTS, shown in UI)."""

from __future__ import annotations

from agent.prompts import normalize_language

# Opening greeting for calls that start in English (the default) — spoken
# as three parts back to back: the English greeting, the same greeting in
# Arabic, then a short Arabic instruction for switching to Arabic via DTMF.
# NOTE: all three parts play through the default English TTS voice/model,
# since language selection hasn't happened yet at this point in the call —
# the Arabic portions may carry an accent as a result. This is a known,
# accepted limitation of the opening line only. Once the caller actually
# presses 2, the rest of the call switches to the properly-configured
# Arabic voice (see entrypoint.py's _switch_to_arabic_async).
_GREETING_EN_PART = "Hi, this is Noura from Good Websites. How can I assist you today?"
_GREETING_AR_PART = "مرحباً، معك نورة من Good Websites. كيف أقدر أساعدك اليوم؟"
_ARABIC_DIAL_INSTRUCTION = "إذا حاب تتكلم بالعربي، اضغط اثنين."

GREETING_EN = f"{_GREETING_EN_PART} {_GREETING_AR_PART} {_ARABIC_DIAL_INSTRUCTION}"

# Spoken once a call has already switched to (or started directly in)
# Arabic — just the Arabic greeting on its own, no English/dial instruction.
GREETING_AR = _GREETING_AR_PART


def get_call_greeting(conversation_language: str = "en") -> str:
    if normalize_language(conversation_language) == "ar":
        return GREETING_AR
    return GREETING_EN