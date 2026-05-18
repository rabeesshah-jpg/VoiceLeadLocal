import pytest
from django.utils import timezone

from apps.calls.services.livekit_tokens import LiveKitTokenService


@pytest.mark.django_db
def test_mint_participant_token(settings):
    settings.LIVEKIT_URL = "wss://test.livekit.cloud"
    settings.LIVEKIT_API_KEY = "APItest"
    settings.LIVEKIT_API_SECRET = "secret_test_key_32chars_minimum_xx"
    settings.VOICE_AGENT_TOKEN_TTL_SECONDS = 600

    svc = LiveKitTokenService()
    result = svc.mint_participant_token("va-abc123", "user-1")
    assert result.token
    assert result.room_name == "va-abc123"
    assert result.identity == "user-1"
    assert result.livekit_url == "wss://test.livekit.cloud"
    assert result.expires_at > timezone.now()


def test_validate_config_missing():
    settings = type("S", (), {"LIVEKIT_URL": "", "LIVEKIT_API_KEY": "", "LIVEKIT_API_SECRET": ""})()
    svc = LiveKitTokenService()
    svc._url = ""
    svc._api_key = ""
    svc._api_secret = ""
    missing = svc.validate_config()
    assert "LIVEKIT_URL" in missing
    assert "LIVEKIT_API_KEY" in missing
