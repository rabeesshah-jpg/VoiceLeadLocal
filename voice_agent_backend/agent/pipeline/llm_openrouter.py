"""OpenRouter LLM via OpenAI-compatible LiveKit plugin."""

from __future__ import annotations

import os

from livekit.plugins import openai

from agent.prompts import get_voice_agent_instructions as build_voice_agent_instructions


def get_voice_agent_instructions(language: str = "en") -> str:
    """System instructions applied when the voice Agent / LLM session starts."""
    return build_voice_agent_instructions(language)


def build_openrouter_llm() -> openai.LLM:
    # openai/gpt-4o-mini is served by only two real providers on OpenRouter:
    # OpenAI's own direct endpoint and Azure. Benchmarked time-to-first-token
    # (Artificial Analysis, checked Aug 2026): OpenAI 1.04s, Azure 1.70s —
    # a clear, consistent gap. Rather than relying only on OpenRouter's
    # per-request latency-sort (which still occasionally routes to Azure or
    # has an off moment), explicitly order OpenAI first. allow_fallbacks
    # stays true so Azure is still used if OpenAI is genuinely down —
    # this only changes which provider is *preferred*, not availability.
    extra_body: dict = {}
    provider_pref: dict = {}
    if os.environ.get("VOICE_AGENT_OPENROUTER_PREFER_OPENAI", "true").lower() in (
        "1", "true", "yes",
    ):
        provider_pref["order"] = ["openai"]
        provider_pref["allow_fallbacks"] = True
    elif os.environ.get("VOICE_AGENT_OPENROUTER_SORT_LATENCY", "true").lower() in (
        "1", "true", "yes",
    ):
        provider_pref["sort"] = "latency"
    if provider_pref:
        extra_body["provider"] = provider_pref

    return openai.LLM(
        model=os.environ.get("VOICE_AGENT_LLM_MODEL", "openai/gpt-4o-mini"),
        api_key=os.environ.get("OPENROUTER_API_KEY"),
        base_url=(os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")).rstrip(
            "/"
        ),
        temperature=0.7,
        max_completion_tokens=256,
        extra_body=extra_body or None,
    )