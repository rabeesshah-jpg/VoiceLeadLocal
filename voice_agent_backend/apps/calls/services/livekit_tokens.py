"""LiveKit access token minting."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from livekit import api

from config.step_log import StepTimer

logger = logging.getLogger("apps.calls.services")


@dataclass
class ParticipantToken:
    token: str
    identity: str
    room_name: str
    livekit_url: str
    expires_at: timezone.datetime


class LiveKitTokenService:
    def __init__(self):
        self._url = settings.LIVEKIT_URL.rstrip("/")
        self._api_key = settings.LIVEKIT_API_KEY
        self._api_secret = settings.LIVEKIT_API_SECRET
        self._ttl = settings.VOICE_AGENT_TOKEN_TTL_SECONDS

    def validate_config(self) -> list[str]:
        with StepTimer(logger, "LIVEKIT", "validate_config"):
            missing = []
            if not self._url:
                missing.append("LIVEKIT_URL")
            if not self._api_key:
                missing.append("LIVEKIT_API_KEY")
            if not self._api_secret:
                missing.append("LIVEKIT_API_SECRET")
            return missing

    def mint_participant_token(
        self,
        room_name: str,
        identity: str,
        *,
        can_publish: bool = True,
        can_subscribe: bool = True,
    ) -> ParticipantToken:
        with StepTimer(
            logger,
            "LIVEKIT",
            "mint_jwt",
            room=room_name,
            identity=identity,
            ttl_sec=self._ttl,
        ):
            expires_at = timezone.now() + timedelta(seconds=self._ttl)
            token = (
                api.AccessToken(self._api_key, self._api_secret)
                .with_identity(identity)
                .with_name(identity)
                .with_ttl(timedelta(seconds=self._ttl))
                .with_grants(
                    api.VideoGrants(
                        room_join=True,
                        room=room_name,
                        can_publish=can_publish,
                        can_subscribe=can_subscribe,
                    )
                )
                .to_jwt()
            )
            return ParticipantToken(
                token=token,
                identity=identity,
                room_name=room_name,
                livekit_url=self._url,
                expires_at=expires_at,
            )

    def mint_agent_token(self, room_name: str, call_id: str) -> ParticipantToken:
        identity = f"agent-{call_id}"
        return self.mint_participant_token(
            room_name,
            identity,
            can_publish=True,
            can_subscribe=True,
        )
