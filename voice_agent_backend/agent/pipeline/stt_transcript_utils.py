"""Clean and validate STT transcripts before UI / LLM."""

from __future__ import annotations

import os
import re


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def min_final_transcript_chars() -> int:
    return _env_int("VOICE_AGENT_STT_MIN_FINAL_CHARS", 3)


def min_final_transcript_words() -> int:
    return _env_int("VOICE_AGENT_STT_MIN_FINAL_WORDS", 2)


def clean_transcript(text: str) -> str:
    """Normalize whitespace and drop obvious STT artifacts."""
    t = (text or "").strip()
    if not t:
        return ""
    t = re.sub(r"\s+", " ", t)
    t = re.sub(r"\(\s*\)", "", t)
    t = re.sub(r"\s+", " ", t).strip()
    return dedupe_consecutive_tokens(t)


def dedupe_consecutive_tokens(text: str) -> str:
    tokens = text.split()
    if not tokens:
        return ""
    out = [tokens[0]]
    for tok in tokens[1:]:
        if tok.lower() == out[-1].lower():
            continue
        out.append(tok)
    return " ".join(out)


def is_duplicate_transcript(previous: str, incoming: str) -> bool:
    prev = clean_transcript(previous)
    inc = clean_transcript(incoming)
    if not inc:
        return True
    if not prev:
        return False
    if transcript_extends_prior(prev, inc):
        return False
    return inc == prev or prev.endswith(inc) or inc in prev


def extract_stt_delta(committed_final: str, incoming: str) -> str:
    """
    Return only new transcript text when STT sends cumulative strings across turns.

    If incoming extends committed_final as a prefix, returns the suffix (trimmed).
    Otherwise returns cleaned incoming (new utterance).
    """
    inc = clean_transcript(incoming)
    if not inc:
        return ""
    prior = clean_transcript(committed_final)
    if not prior:
        return inc
    if inc == prior:
        return ""
    if inc.startswith(prior):
        delta = inc[len(prior) :].strip()
        return clean_transcript(delta)
    return inc


def transcript_extends_prior(prior: str, incoming: str, *, min_growth_chars: int = 4) -> bool:
    """True when incoming is the same utterance continued (longer than prior)."""
    prev = clean_transcript(prior)
    inc = clean_transcript(incoming)
    if not prev or not inc:
        return False
    if len(inc) <= len(prev) + max(0, min_growth_chars - 1):
        return False
    return inc.startswith(prev)


def is_duplicate_final_for_send(
    *,
    delta: str,
    last_sent_final: str,
    last_committed_cumulative: str,
) -> tuple[bool, str]:
    """
    Return (is_duplicate, reason) before sending a final delta to UI/LLM.

    Blocks exact resends, text already covered by last_sent, and non-growing
    repeats of the committed cumulative transcript.
    """
    d = clean_transcript(delta)
    if not d:
        return True, "empty"
    sent = clean_transcript(last_sent_final)
    if sent:
        if d == sent:
            return True, "already_sent"
        if d in sent and len(d) <= len(sent):
            return True, "already_sent"
        if sent in d and len(d) <= len(sent) + 2:
            return True, "already_sent"
    committed = clean_transcript(last_committed_cumulative)
    if committed:
        if d == committed:
            return True, "same_as_committed"
        if is_duplicate_transcript(committed, d) and not transcript_extends_prior(
            committed, d
        ):
            return True, "duplicate_cumulative"
    return False, ""


def validate_final_transcript(text: str) -> tuple[bool, str]:
    """
    Reject garbled or too-short finals before UI/LLM.

    Returns (ok, reason).
    """
    cleaned = clean_transcript(text)
    if not cleaned:
        return False, "empty"

    if len(cleaned) < min_final_transcript_chars():
        return False, "too_short"

    words = cleaned.split()
    if len(words) < min_final_transcript_words():
        return False, "too_few_words"

    if _looks_garbled(cleaned):
        return False, "garbled"

    return True, "ok"


def _looks_garbled(text: str) -> bool:
    words = text.lower().split()
    if len(words) < 3:
        return False

    # Repeated bigrams like "my name my name"
    for i in range(len(words) - 3):
        if words[i : i + 2] == words[i + 2 : i + 4]:
            return True

    unique_ratio = len(set(words)) / len(words)
    if len(words) >= 6 and unique_ratio < 0.45:
        return True

    return False
