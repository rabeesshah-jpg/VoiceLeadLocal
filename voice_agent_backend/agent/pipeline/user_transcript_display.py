"""English copy of caller speech for the chat UI (agent still uses raw STT)."""

from __future__ import annotations

import logging
import os

import httpx

from agent.pipeline.user_stt_router import dominant_user_script

logger = logging.getLogger("agent.user_transcript_display")

_TRANSLATE_SYSTEM = (
    "Translate the caller's message into natural English for a support chat UI. "
    "Output only the English text — no quotes, labels, or commentary."
)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def user_ui_english_enabled() -> bool:
    return _env_bool("VOICE_AGENT_USER_UI_ENGLISH", True)


def needs_english_ui_translation(text: str) -> bool:
    """True when the transcript is not already suitable to show as English."""
    stripped = text.strip()
    if not stripped:
        return False
    script = dominant_user_script(stripped)
    if script in ("arabic", "devanagari"):
        return True
    if script == "latin":
        return False
    return not any(c.isascii() and c.isalpha() for c in stripped)


async def translate_to_english(text: str) -> str:
    api_key = (os.environ.get("OPENROUTER_API_KEY") or "").strip()
    if not api_key:
        logger.warning("OPENROUTER_API_KEY missing; user UI transcript not translated")
        return text.strip()

    base = (os.environ.get("OPENROUTER_BASE_URL") or "https://openrouter.ai/api/v1").rstrip("/")
    model = os.environ.get("VOICE_AGENT_TRANSLATE_MODEL") or os.environ.get(
        "VOICE_AGENT_LLM_MODEL", "openai/gpt-4o-mini"
    )
    timeout = float(os.environ.get("VOICE_AGENT_TRANSLATE_TIMEOUT_SEC", "8"))

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base}/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": model,
                    "temperature": 0,
                    "max_tokens": 256,
                    "messages": [
                        {"role": "system", "content": _TRANSLATE_SYSTEM},
                        {"role": "user", "content": text.strip()},
                    ],
                },
            )
            response.raise_for_status()
            payload = response.json()
            content = payload["choices"][0]["message"]["content"]
            translated = (content or "").strip()
            return translated or text.strip()
    except Exception as exc:
        logger.warning("User UI translation failed: %s", exc)
        return text.strip()


async def user_message_for_ui(text: str, *, is_final: bool) -> str | None:
    """
    Text to publish on the user_transcript data channel.

    Partials stream immediately (raw STT) so the UI and pipeline react without
    waiting for OpenRouter translation or STT finalization.
    """
    stripped = text.strip()
    if not stripped:
        return None

    if not user_ui_english_enabled():
        return stripped

    if not needs_english_ui_translation(stripped):
        return stripped

    if not is_final:
        return stripped

    return await translate_to_english(stripped)
