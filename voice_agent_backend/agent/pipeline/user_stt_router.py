"""Pick Deepgram STT locale for caller speech (independent of agent voice language)."""

from __future__ import annotations

import re
import unicodedata

from agent.prompts import normalize_language

# Unicode script buckets used to route streaming STT on Arabic calls (en + ar).
_ARABIC_RE = re.compile(
    r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]"
)
_LATIN_RE = re.compile(r"[A-Za-z]")
_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")


def _letter_counts(text: str) -> dict[str, int]:
    arabic = len(_ARABIC_RE.findall(text))
    latin = len(_LATIN_RE.findall(text))
    devanagari = len(_DEVANAGARI_RE.findall(text))
    letters = 0
    for ch in text:
        if ch.isalpha() and unicodedata.category(ch).startswith("L"):
            letters += 1
    return {
        "arabic": arabic,
        "latin": latin,
        "devanagari": devanagari,
        "letters": letters,
    }


def dominant_user_script(text: str) -> str:
    """Best-effort script label for routing STT on multilingual calls."""
    counts = _letter_counts(text)
    if counts["letters"] == 0:
        return "unknown"

    if counts["devanagari"] > 0 and counts["devanagari"] >= counts["latin"]:
        return "devanagari"

    if counts["arabic"] > counts["latin"]:
        return "arabic"
    if counts["latin"] > counts["arabic"]:
        return "latin"
    if counts["arabic"] > 0:
        return "arabic"
    if counts["latin"] > 0:
        return "latin"
    return "unknown"


def default_user_stt_language(conversation_language: str) -> str:
    """
    Initial Deepgram language before caller speech is analyzed.

    Arabic *calls* also start on en-US so English is not locked into Arabic script;
    entrypoint switches to ar-SA when Arabic script appears in the transcript.
    """
    _ = normalize_language(conversation_language)
    return "en-US"


def resolve_user_stt_language_from_text(
    text: str,
    *,
    conversation_language: str,
    current_language: str,
) -> str:
    """
    Choose Deepgram STT language for the *next* audio segment.

    Arabic calls use ar-SA by default; switch to en-US when Latin dominates so
    English callers are not forced into Arabic script. Never use ``multi`` here:
    Nova-3 multi does not include Arabic and can mislabel Arabic as Hindi.
    """
    if normalize_language(conversation_language) != "ar":
        return "en-US"

    script = dominant_user_script(text)
    if script == "latin":
        return "en-US"
    if script in ("arabic", "devanagari", "unknown"):
        return "ar-SA"
    return current_language


def is_likely_mistranscribed_for_ar_call(text: str) -> bool:
    """True when STT output looks like a wrong locale (e.g. Hindi on Arabic speech)."""
    if dominant_user_script(text) == "devanagari":
        counts = _letter_counts(text)
        return counts["devanagari"] >= 2
    return False
