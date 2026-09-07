from __future__ import annotations
import os
from livekit.plugins import openai
from agent.prompts import get_voice_agent_instructions as build_voice_agent_instructions
from agent.pipeline.llm_http_pool import get_shared_openai_client


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
        "default_model": "openai/gpt-oss-20b",
    },
    "cerebras": {
        "base_url": "https://api.cerebras.ai/v1",
        "key_env": "CEREBRAS_API_KEY",
        "default_model": "gpt-oss-120b",
    },
}

# gpt-oss models (used by Groq and Cerebras above) are reasoning models.
# Without capping reasoning effort, they can spend the entire
# max_completion_tokens budget on invisible reasoning tokens and emit zero
# actual answer text — item.text_content ends up "" and the turn is
# silently dropped downstream (no TTS, agent just goes back to listening).
_REASONING_PROVIDERS = {"groq", "cerebras"}


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
    base_url = cfg["base_url"]

    # Reuse a process-wide pooled AsyncOpenAI client instead of letting the
    # plugin build its own per-session client with the SDK's default 5s
    # httpx keepalive_expiry. That default was causing a fresh TCP+TLS
    # handshake (~350-400ms, confirmed via curl) on turns separated by
    # normal conversational pauses. This client is keyed by (api_key,
    # base_url) and shared across calls/turns for this worker process.
    shared_client = get_shared_openai_client(api_key=api_key, base_url=base_url)

    kwargs = dict(
        model=model,
        client=shared_client,
        temperature=0.3,
        max_completion_tokens=120,
        parallel_tool_calls=True,
    )

    if provider in _REASONING_PROVIDERS:
        # Keep reasoning minimal for real-time voice — we want a fast
        # spoken answer, not deliberation — and give enough token headroom
        # for reasoning + the actual answer so the answer doesn't get
        # truncated to empty.
        kwargs["reasoning_effort"] = "low"
        kwargs["max_completion_tokens"] = 300

    return openai.LLM(**kwargs)