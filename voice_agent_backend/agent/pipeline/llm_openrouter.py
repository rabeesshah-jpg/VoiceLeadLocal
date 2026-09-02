"""Direct OpenAI LLM via the LiveKit openai plugin (no OpenRouter)."""

from __future__ import annotations

import os

from livekit.plugins import openai

from agent.prompts import get_voice_agent_instructions as build_voice_agent_instructions


def get_voice_agent_instructions(language: str = "en") -> str:
    """System instructions applied when the voice Agent / LLM session starts."""
    return build_voice_agent_instructions(language)


def build_openrouter_llm() -> openai.LLM:
    # Direct OpenAI (no OpenRouter) — isolates whether OpenRouter's own
    # measured ~0.55-0.7s "Routing Overhead" (confirmed on their own
    # Activity dashboard) is actually removable by going direct, or an
    # unavoidable property of the model/generation itself.
    #
    # gpt-4.1-nano: a genuine lightweight, non-reasoning model (NOT
    # gpt-5-nano, which is a reasoning model and would likely be slower
    # despite the name).
    #
    # NOTE: function name kept as build_openrouter_llm() to avoid touching
    # the import in entrypoint.py — this builds a direct OpenAI client.
    #
    # NOTE: model string has NO "openai/" prefix here — that prefix is an
    # OpenRouter-only convention for identifying provider+model. Direct
    # OpenAI's own API uses the bare model name.
    return openai.LLM(
        model=os.environ.get("VOICE_AGENT_LLM_MODEL", "gpt-4.1-nano"),
        api_key=os.environ.get("OPENAI_API_KEY"),
        temperature=0.3,
        max_completion_tokens=120,
        parallel_tool_calls=True,
    )