"""Deterministic post-call lead extraction — a backstop for save_lead_info's
live tool-calling reliability, which testing has shown fails intermittently
even under the best-known pipeline settings.

Runs once, after the call ends, using the full conversation transcript
(accumulated live during the call, not re-parsed from log files). Only
fills fields that are still empty on the Lead row, so it never overwrites
anything save_lead_info already correctly captured live during the call —
this is purely a safety net for whatever save_lead_info missed.
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

_SYSTEM_PROMPT = """You extract structured lead information from a phone/voice call transcript between "NORA" (a sales qualifying assistant for a website agency called Good Websites) and "USER" (the caller).

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
- lead_intent: "strong fit", "moderate", or "weak" based on their interest level shown in the transcript

Only use information actually stated in the transcript. Do not guess, invent, or infer values that weren't said. Return raw JSON only — no markdown code fences, no explanation, just the JSON object."""


def extract_lead_fields(transcript: str) -> dict:
    """Call the LLM once with the full transcript and return extracted
    fields as a dict (only keys with real, non-empty values are included).

    Never raises — returns {} on any failure. This is a best-effort
    backstop and must never break call shutdown.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    base_url = (
        os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1"
    ).rstrip("/")
    model = os.environ.get("VOICE_AGENT_LLM_MODEL", "openai/gpt-4o-mini")

    if not api_key or not transcript.strip():
        return {}

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": transcript},
        ],
        "temperature": 0,
        "max_completion_tokens": 400,
    }
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
        return {
            k: v for k, v in data.items() if k in FIELDS and v not in (None, "")
        }
    except Exception:
        logger.exception("lead_extraction: extraction call failed")
        return {}