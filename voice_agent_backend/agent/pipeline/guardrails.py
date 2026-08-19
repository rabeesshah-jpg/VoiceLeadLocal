"""Deterministic guardrail patterns — a second layer beneath the system
prompt's own guardrails instructions.

If the prompt-level guardrails ever fail to stop an off-topic question or a
prompt-injection/jailbreak attempt, this catches known trigger phrases in
the caller's own words and returns a fixed refusal WITHOUT calling the LLM
at all. That makes this layer both cheaper and faster than a normal turn
(no model round-trip), and immune to the model being talked into
complying, since no reasoning is involved — it's a plain pattern match.

This is intentionally narrow: it only catches clear injection/jailbreak
phrasing, not every possible off-topic question (that's the prompt's job).
Add more patterns here over time as real attempts show up in call logs.
"""

from __future__ import annotations

import re

_TRIGGER_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bignore (all |your |previous |the )?(instructions|prompt|rules)\b",
        r"\bdisregard (your |all |previous )?(instructions|prompt|rules)\b",
        r"\bforget (your |all |everything|previous )?(instructions|prompt|rules)?\b",
        r"\bnew instructions\b",
        r"\byou are now\b",
        r"\bact as\b",
        r"\bpretend (you|to be)\b",
        r"\broleplay as\b",
        r"\bdeveloper mode\b",
        r"\bdan mode\b",
        r"\bsystem prompt\b",
        r"\b(what|reveal|show|tell|print|repeat)\b.{0,20}\byour (instructions|prompt|rules)\b",
        r"\byour (instructions|prompt|rules)\b.{0,20}\b(what|reveal|show|tell|print|repeat)\b",
        r"\bdo anything now\b",
        r"\bno restrictions\b",
        r"\bwithout restrictions\b",
        r"\boverride your\b",
        r"\bchange your (instructions|prompt|rules|role)\b",
        r"\bupdate your (instructions|prompt|rules)\b",
        r"\bi(?:'m| am) (the )?(developer|admin|owner|creator)\b",
    )
)

REFUSAL_EN = (
    "Sorry, I'm not able to help with that — I'm just here to help with your website project."
)
REFUSAL_AR = "عذراً، ما أقدر أساعدك بهذا — أنا هنا فقط للمساعدة بخصوص مشروع موقعك."


def is_guardrail_trigger(text: str) -> bool:
    """True if the given user text matches a known jailbreak/injection pattern."""
    if not text or not text.strip():
        return False
    return any(p.search(text) for p in _TRIGGER_PATTERNS)


def refusal_text(language: str) -> str:
    return REFUSAL_AR if language == "ar" else REFUSAL_EN