"""
Conversation state machine labels for UI / logging.

The LiveKit AgentSession handles STT → LLM → TTS pipelining, endpointing,
and barge-in. This module maps SDK states to our data-channel contract.
"""

from __future__ import annotations

from typing import Literal

AgentUiState = Literal[
    "initializing",
    "idle",
    "listening",
    "thinking",
    "speaking",
    "interrupted",
]


def map_sdk_state(sdk_state: str, *, was_speaking: bool = False) -> AgentUiState:
    if sdk_state == "listening" and was_speaking:
        return "interrupted"
    if sdk_state in ("initializing", "idle", "listening", "thinking", "speaking"):
        return sdk_state  # type: ignore[return-value]
    return "listening"
