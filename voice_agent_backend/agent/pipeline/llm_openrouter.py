"""LLM client via the LiveKit openai plugin, pointed at whichever
OpenAI-compatible provider is selected in .env.

Provider is chosen via VOICE_AGENT_LLM_PROVIDER (openai / groq / cerebras,
defaults to openai). Each provider reads its own API key from its own env
var, so switching providers is a two-line .env change with no code edit:

    VOICE_AGENT_LLM_PROVIDER=groq
    VOICE_AGENT_LLM_MODEL=qwen/qwen3.8-27b

    VOICE_AGENT_LLM_PROVIDER=cerebras
    VOICE_AGENT_LLM_MODEL=gpt-oss-120b

    VOICE_AGENT_LLM_PROVIDER=openai   (or unset)
    VOICE_AGENT_LLM_MODEL=gpt-4.1-nano

NOTE: function name kept as build_openrouter_llm() to avoid touching the
import in entrypoint.py — despite the name, this builds a plain OpenAI-
compatible client for whichever provider is configured, not OpenRouter.

NOTE: model strings are provider-specific. OpenAI and Cerebras use the
bare model name (e.g. "gpt-4.1-nano", "gpt-oss-120b"). Groq's own hosted
OpenAI/Qwen models use a provider-prefixed id (e.g. "openai/gpt-oss-20b",
"qwen/qwen3.8-27b") — that prefix is Groq's own catalog convention,
unrelated to OpenRouter's.

reasoning_effort: several Groq-hosted models (the gpt-oss family and the
qwen3 family) are reasoning models that reject requests with no explicit
reasoning_effort, or with a value outside what that specific model
supports — each model has a DIFFERENT allowed set, so this has to be
looked up per model, not per provider:
    - openai/gpt-oss-20b, openai/gpt-oss-120b: low / medium / high only
      (no "none" — sending "none" 400s)
    - qwen/qwen3.6-27b: none / default / null only
      (no low/medium/high — sending those 400s)
    - qwen/qwen3.8-27b: none / default / low / medium / high
Leaving reasoning_effort unset does not reliably fall back to a safe
value across these models (confirmed by a live 400 from qwen3.6-27b with
nothing explicitly set), so we always send an explicit value for any
model in the table below. Models not in the table (e.g. plain OpenAI,
Cerebras's non-reasoning models) never get this kwarg at all.
"""

from __future__ import annotations

import os

from livekit.plugins import openai

from agent.prompts import get_voice_agent_instructions as build_voice_agent_instructions


def get_voice_agent_instructions(language: str = "en") -> str:
    """System instructions applied when the voice Agent / LLM session starts."""
    return build_voice_agent_instructions(language)


# base_url=None means "use the openai plugin's own default", i.e. direct
# OpenAI — matches the original hardcoded behavior exactly when
# VOICE_AGENT_LLM_PROVIDER is unset.
_PROVIDER_CONFIG: dict[str, dict[str, str | None]] = {
    "openai": {
        "base_url": None,
        "key_env": "OPENAI_API_KEY",
        "default_model": "gpt-4.1-nano",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
        # qwen3.8-27b over qwen3.6-27b as the default: 3.8 tolerates a
        # wider range of reasoning_effort values, and the gpt-oss family
        # is avoided as a default entirely — it uses OpenAI's Harmony
        # tool-call format, which has a documented, currently-open bug
        # where special formatting tokens (e.g. "<|channel|>commentary")
        # intermittently leak into the tool name string on Groq's serving
        # stack, corrupting tool calls unpredictably mid-conversation.
        "default_model": "qwen/qwen3.8-27b",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "key_env": "CEREBRAS_API_KEY",
        "default_model": "gpt-oss-120b",
    },
}

# Per-model, not per-provider — each reasoning model on Groq has a
# different allowed set of values, see module docstring.
_GROQ_REASONING_EFFORT: dict[str, str] = {
    "openai/gpt-oss-20b": "low",
    "openai/gpt-oss-120b": "low",
    "qwen/qwen3.6-27b": "none",
    "qwen/qwen3.8-27b": "none",
}


def _reasoning_effort_for(provider: str, model: str) -> str | None:
    if provider != "groq":
        return None
    return _GROQ_REASONING_EFFORT.get(model)


def build_openrouter_llm() -> openai.LLM:
    provider = (os.environ.get("VOICE_AGENT_LLM_PROVIDER") or "openai").strip().lower()
    cfg = _PROVIDER_CONFIG.get(provider)
    if cfg is None:
        raise ValueError(
            f"Unknown VOICE_AGENT_LLM_PROVIDER={provider!r}; "
            f"expected one of {sorted(_PROVIDER_CONFIG)}"
        )

    api_key = os.environ.get(cfg["key_env"])
    if not api_key:
        raise ValueError(
            f"{cfg['key_env']} is required for provider={provider!r} "
            f"(set it in .env, or set VOICE_AGENT_LLM_PROVIDER to a "
            f"provider whose key you have configured)"
        )

    model = os.environ.get("VOICE_AGENT_LLM_MODEL") or cfg["default_model"]

    kwargs = dict(
        model=model,
        api_key=api_key,
        temperature=0.3,
        max_completion_tokens=120,
        parallel_tool_calls=True,
    )
    if cfg["base_url"]:
        kwargs["base_url"] = cfg["base_url"]

    reasoning_effort = _reasoning_effort_for(provider, model)
    if reasoning_effort is not None:
        kwargs["reasoning_effort"] = reasoning_effort

    return openai.LLM(**kwargs)