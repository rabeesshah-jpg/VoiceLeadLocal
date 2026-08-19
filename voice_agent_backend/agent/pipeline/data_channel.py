"""Publish JSON events to room participants via LiveKit data packets."""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from livekit import rtc

logger = logging.getLogger("agent.data_channel")


class DataChannelPublisher:
    def __init__(self, room: "rtc.Room"):
        self._room = room

    async def publish(self, payload: dict, *, reliable: bool = True):
        try:
            data = json.dumps(payload).encode("utf-8")
            await self._room.local_participant.publish_data(data, reliable=reliable)
        except Exception as exc:
            logger.warning("Failed to publish data message: %s", exc)

    async def agent_state(self, state: str):
        await self.publish({"type": "agent_state", "state": state})

    async def user_transcript(
        self,
        text: str,
        *,
        is_final: bool = False,
        message_id: str = "",
        turn_seq: int = 0,
    ):
        payload: dict = {
            "type": "user_transcript",
            "text": text,
            "is_final": is_final,
            "ts": time.time(),
        }
        if message_id:
            payload["message_id"] = message_id
        if turn_seq:
            payload["turn_seq"] = turn_seq
        # Interim (non-final) transcripts fire many times per second while the
        # caller is talking — each one supersedes the last, so losing one is
        # harmless. Reliable delivery's per-message ack round-trip isn't worth
        # paying at that frequency; only the final transcript needs it.
        await self.publish(payload, reliable=is_final)

    async def agent_text(self, text: str, is_final: bool = False):
        await self.publish({"type": "agent_text", "text": text, "is_final": is_final})

    async def llm_response(self, text: str, *, is_final: bool = False):
        # Same reasoning as user_transcript: streamed partial LLM text fires
        # on every token. Awaiting a reliable (acked) publish for each one
        # was serializing the entire generation loop behind network
        # round-trips — this is the primary fix for the ~300ms/token
        # slowdown seen in pipeline latency logs. Only the final chunk needs
        # guaranteed delivery.
        await self.publish(
            {
                "type": "llm_response",
                "text": text,
                "is_final": is_final,
                "ts": time.time(),
            },
            reliable=is_final,
        )

    async def llm_playback_end(self, *, interrupted: bool = False):
        """Signal that TTS/audio playout ended (or was interrupted)."""
        await self.publish(
            {
                "type": "llm_playback_end",
                "interrupted": interrupted,
                "ts": time.time(),
            }
        )

    async def latency(self, metrics: dict):
        await self.publish({"type": "latency", **metrics})

    async def llm_start(self, *, turn_id: str, llm_start_ms: int) -> None:
        await self.publish(
            {
                "type": "llm_start",
                "turn_id": turn_id,
                "llm_start_ms": llm_start_ms,
                # Legacy field name for older frontends.
                "llm_first_token_ms": llm_start_ms,
                "ts": time.time(),
            }
        )

    async def error(self, code: str, message: str):
        await self.publish({"type": "error", "code": code, "message": message})