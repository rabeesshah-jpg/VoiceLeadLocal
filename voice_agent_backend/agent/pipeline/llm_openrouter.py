"""OpenRouter LLM via OpenAI-compatible LiveKit plugin."""

from __future__ import annotations

import os

from livekit.plugins import openai


def build_openrouter_llm() -> openai.LLM:
    return openai.LLM(
        model=os.environ.get("VOICE_AGENT_LLM_MODEL", "openai/gpt-4o-mini"),
        api_key=os.environ.get("OPENROUTER_API_KEY"),
        base_url=(os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")).rstrip(
            "/"
        ),
        temperature=0.7,
        max_completion_tokens=256,
    )
