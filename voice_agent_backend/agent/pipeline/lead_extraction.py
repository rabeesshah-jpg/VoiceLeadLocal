"""Deterministic post-call lead extraction, a backstop for save_lead_info's
live tool-calling reliability, which testing has shown fails intermittently
even under the best-known pipeline settings.

Runs once, after the call ends, using the full conversation transcript
(accumulated live during the call, not re-parsed from log files). Only
fills fields that are still empty on the Lead row, so it never overwrites
anything save_lead_info already correctly captured live during the call.
This is purely a safety net for whatever save_lead_info missed.

Provider resolution: prefers direct OpenAI (OPENAI_API_KEY), then
OpenRouter (OPENROUTER_API_KEY), then Groq (GROQ_API_KEY) as a last
resort. Groq was added after a real incident: the live call's LLM
provider had been switched to Groq, OPENAI_API_KEY was unset (billing
exhausted) and OPENROUTER_API_KEY was never configured, so this backstop
had silently returned {} on every call for an unknown period — the one
call where save_lead_info also failed lost its lead entirely, with
nothing to recover it, because the safety net itself had no working key.
Every early-return path below now logs a clear reason so this can't
happen silently again; check for "extraction disabled" in the logs
whenever changing which LLM provider the live call uses.
"""

from __future__ import annotations

import json
import logging
import os

import requests

logger = logging.getLogger("agent.lead_extraction")

FIELDS: tuple[str, ...] = (
    "name",
    "company",
    "whatsapp_number",
    "city",
    "need",
    "has_existing_website",
    "website_action",
    "business_description",
    "start_timeline",
    "lead_intent",
)

_OPENAI_BASE = "https://api.openai.com/v1"
_OPENROUTER_BASE = "https://openrouter.ai/api/v1"
_GROQ_BASE = "https://api.groq.com/openai/v1"

# Groq's reasoning models each accept a DIFFERENT set of reasoning_effort
# values — sending the wrong one is a hard 400, not a soft fallback (this
# is what broke the live call this backstop is meant to catch). Kept in
# sync with the equivalent table in agent/pipeline/llm_openrouter.py.
_GROQ_REASONING_EFFORT: dict[str, str] = {
    "openai/gpt-oss-20b": "low",
    "openai/gpt-oss-120b": "low",
    "qwen/qwen3.6-27b": "none",
    "qwen/qwen3.8-27b": "none",
}
# Extraction here is a plain JSON completion, no tool/function calling, so
# it isn't exposed to the gpt-oss Harmony tool-name leak bug — but we still
# default to qwen3.8-27b for consistency with the live pipeline's proven
# choice, and because it tolerates a wider reasoning_effort range than
# qwen3.6 if VOICE_AGENT_LLM_MODEL points at something Groq-specific.
_GROQ_DEFAULT_MODEL = "qwen/qwen3.8-27b"

_SYSTEM_PROMPT = """You extract structured lead information from a phone/voice call transcript between "NOURA" (a sales qualifying assistant for a website agency called Good Websites) and "USER" (the caller).

Return ONLY a JSON object with these fields, using null for anything not mentioned or unclear:
- name: caller's name
- company: caller's company name
- whatsapp_number: a phone number the caller stated, in clean international format (assume +92 Pakistan if a local-style number is given with no country code); null if no number was ever stated in the transcript
- city: caller's city
- need: what they need (e.g. "New website")
- has_existing_website: true, false, or null if not discussed
- website_action: "upgrade", "new", or null
- business_description: what the caller's business does
- start_timeline: when they want to start
- lead_intent: "strong fit", "moderate", or "weak" based on their interest level shown in the transcript. Strong fit means they asked about a quote, pricing or a consultation, described a real project, shared contact details, or want to start soon. Moderate means they are exploring services but not ready to book. Weak means vague curiosity with very little detail.

Only use information actually stated in the transcript. Do not guess, invent, or infer values that weren't said. Return raw JSON only, no markdown code fences, no explanation, just the JSON object."""


def _resolve_provider() -> tuple[str, str, str, str | None] | None:
    """Return (api_key, base_url, model, reasoning_effort) for whichever
    provider is configured, or None if none are. Order: OpenAI, then
    OpenRouter, then Groq.
    """
    model = (os.environ.get("VOICE_AGENT_LLM_MODEL") or "").strip()

    openai_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if openai_key:
        base = (os.environ.get("OPENAI_BASE_URL") or _OPENAI_BASE).rstrip("/")
        m = model or "gpt-4.1-nano"
        if m.startswith("openai/"):
            m = m.split("/", 1)[1]
        return openai_key, base, m, None

    router_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if router_key:
        base = (os.environ.get("OPENROUTER_BASE_URL") or _OPENROUTER_BASE).rstrip("/")
        m = model or "gpt-4o-mini"
        if "/" not in m:
            m = f"openai/{m}"
        return router_key, base, m, None

    groq_key = (os.environ.get("GROQ_API_KEY") or "").strip()
    if groq_key:
        base = (os.environ.get("GROQ_BASE_URL") or _GROQ_BASE).rstrip("/")
        # Only reuse VOICE_AGENT_LLM_MODEL if it's actually a Groq model id
        # (contains a "/"), otherwise it's leftover from a different
        # provider (e.g. "gpt-4.1-nano") and would 404 against Groq.
        m = model if model and "/" in model else _GROQ_DEFAULT_MODEL
        reasoning_effort = _GROQ_REASONING_EFFORT.get(m)
        return groq_key, base, m, reasoning_effort

    return None


def extract_lead_fields(transcript: str) -> dict:
    """Call the LLM once with the full transcript and return extracted
    fields as a dict (only keys with real, non-empty values are included).

    Never raises, returns {} on any failure. This is a best-effort
    backstop and must never break call shutdown. Every no-op path logs a
    reason, so a misconfigured or missing key cannot silently disable
    extraction without leaving a trace in the logs.
    """
    if not transcript.strip():
        logger.warning("lead_extraction: empty transcript, skipping")
        return {}

    provider = _resolve_provider()
    if provider is None:
        logger.error(
            "lead_extraction: no API key configured "
            "(set OPENAI_API_KEY, OPENROUTER_API_KEY, or GROQ_API_KEY), "
            "extraction disabled"
        )
        return {}

    api_key, base_url, model, reasoning_effort = provider

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
        "temperature": 0,
        "max_completion_tokens": 400,
    }
    if reasoning_effort is not None:
        payload["reasoning_effort"] = reasoning_effort

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        resp = requests.post(
            f"{base_url}/chat/completions",
            headers=headers,
            json=payload,
            timeout=15,
        )
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"].strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.lower().startswith("json"):
                content = content[4:]
        data = json.loads(content)
        extracted = {
            k: v for k, v in data.items() if k in FIELDS and v not in (None, "")
        }
        logger.info(
            "lead_extraction: extracted %s field(s) model=%s fields=%s",
            len(extracted), model, sorted(extracted),
        )
        return extracted
    except Exception:
        logger.exception("lead_extraction: extraction call failed model=%s", model)
        return {}