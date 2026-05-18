"""Publish JSON events to room participants via LiveKit data packets."""

from __future__ import annotations

import json
import logging
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

    async def user_transcript(self, text: str, is_final: bool = False):
        await self.publish({"type": "user_transcript", "text": text, "is_final": is_final})

    async def agent_text(self, text: str, is_final: bool = False):
        await self.publish({"type": "agent_text", "text": text, "is_final": is_final})

    async def latency(self, metrics: dict):
        await self.publish({"type": "latency", **metrics})

    async def error(self, code: str, message: str):
        await self.publish({"type": "error", "code": code, "message": message})
