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

    async def publish(self, payload: dict):
        try:
            data = json.dumps(payload).encode("utf-8")
            await self._room.local_participant.publish_data(data, reliable=True)
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
        await self.publish(payload)

    async def agent_text(self, text: str, is_final: bool = False):
        await self.publish({"type": "agent_text", "text": text, "is_final": is_final})

    async def llm_response(self, text: str, *, is_final: bool = False):
        await self.publish(
            {
                "type": "llm_response",
                "text": text,
                "is_final": is_final,
                "ts": time.time(),
            }
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
