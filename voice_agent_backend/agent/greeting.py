"""Fixed call-opening greeting (spoken via TTS, shown in UI)."""

from __future__ import annotations

from agent.prompts import normalize_language

GREETING_EN = (
    "Hello, I'm Noura from Good Websites. How can I assist you today?"
)
GREETING_AR = (
    "مرحباً، معك نورة من Good Websites. كيف أقدر أساعدك اليوم؟"
)


def get_call_greeting(conversation_language: str = "en") -> str:
    if normalize_language(conversation_language) == "ar":
        return GREETING_AR
    return GREETING_EN
