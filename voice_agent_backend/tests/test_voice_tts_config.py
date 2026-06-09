import pytest

from apps.calls.models import CallSession, VoiceProfile
from apps.calls.services.voice_tts_config import (
    build_tts_metadata_for_call,
    effective_tts_voice,
    merge_tts_config_from_sources,
)


def test_effective_tts_voice_prefers_provider():
    cfg = {"voice": "M1", "fallback_voice": "M1", "provider_voice_id": "uuid-abc"}
    assert effective_tts_voice(cfg) == "uuid-abc"


def test_build_tts_metadata_custom():
    profile = VoiceProfile(
        name="Test",
        status=VoiceProfile.STATUS_READY,
        provider_voice_id="uuid-test",
    )
    cfg = build_tts_metadata_for_call(
        persona_id="male",
        language="en",
        voice_mode="custom",
        voice_profile=profile,
        fallback_voice="M1",
    )
    assert cfg["voice_mode"] == "custom"
    assert cfg["provider_voice_id"] == "uuid-test"
    assert effective_tts_voice(cfg) == "uuid-test"


@pytest.mark.django_db
def test_merge_tts_config_from_db_session():
    profile = VoiceProfile.objects.create(
        name="Clone",
        status=VoiceProfile.STATUS_READY,
        provider_voice_id="uuid-db",
    )
    session = CallSession.objects.create(
        room_name="va-test",
        user_identity="user-test",
        persona_id="male",
        voice_mode="custom",
        voice_profile=profile,
        provider_voice_id="uuid-db",
        fallback_voice="M1",
        language="en",
    )
    session = CallSession.objects.select_related("voice_profile").get(pk=session.pk)
    cfg = merge_tts_config_from_sources(
        language="en",
        persona_id="male",
        meta={},
        db_session=session,
    )
    assert cfg["voice_mode"] == "custom"
    assert cfg["provider_voice_id"] == "uuid-db"
    assert effective_tts_voice(cfg) == "uuid-db"
