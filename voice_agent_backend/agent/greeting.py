"""Fixed call-opening greeting (spoken via TTS, shown in UI)."""

from __future__ import annotations

from agent.prompts import normalize_language

# Opening greeting for calls that start in English (the default): the
# English greeting only, followed by a short Arabic instruction for
# switching to Arabic via DTMF, in real Arabic script.
#
# This line still plays through the DEFAULT ENGLISH TTS voice/model, since
# language selection hasn't happened yet at this point in the call — it
# may carry an accent as a known, accepted limitation of this one line.
# Once the caller actually presses 2, the rest of the call switches to the
# properly-configured Arabic voice (Fatima) — see entrypoint.py's
# _switch_to_arabic_async.
_GREETING_EN_PART = "Hi, this is Noura from Good Websites. How can I assist you today?"
_ARABIC_DIAL_INSTRUCTION = "للتحدث باللغة العربية، يرجى طلب الرقم 2."

GREETING_EN = f"{_GREETING_EN_PART} {_ARABIC_DIAL_INSTRUCTION}"

# Spoken once a call has already switched to (or started directly in)
# Arabic — real Arabic script, spoken through the properly-configured
# Arabic voice/model, so no transliteration concern applies here.
GREETING_AR = "مرحباً، معك نورة من Good Websites. كيف أقدر أساعدك اليوم؟"


def get_call_greeting(conversation_language: str = "en") -> str:
    if normalize_language(conversation_language) == "ar":
        return GREETING_AR
    return GREETING_EN