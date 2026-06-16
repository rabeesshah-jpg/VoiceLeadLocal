import pytest

from apps.calls.services.tts_voice_clone import resolve_voice_profile_tts_base_url


@pytest.mark.parametrize(
    "voice_profile_url,tts_url,chatterbox,expected",
    [
        ("http://runpod.example:7788", "", "", "http://runpod.example:7788"),
        ("", "http://legacy.example:7788", "", "http://legacy.example:7788"),
        ("", "", "http://chatterbox.example:8000", "http://chatterbox.example:8000"),
        ("http://preferred.example:1/", "http://other.example:2", "", "http://preferred.example:1"),
    ],
)
def test_resolve_voice_profile_tts_base_url_precedence(
    settings, voice_profile_url, tts_url, chatterbox, expected
):
    settings.VOICE_PROFILE_TTS_BASE_URL = voice_profile_url
    settings.TTS_BASE_URL = tts_url
    settings.CHATTERBOX_TTS_URL = chatterbox
    assert resolve_voice_profile_tts_base_url() == expected


def test_resolve_voice_profile_tts_base_url_empty(settings):
    settings.VOICE_PROFILE_TTS_BASE_URL = ""
    settings.TTS_BASE_URL = ""
    settings.CHATTERBOX_TTS_URL = ""
    assert resolve_voice_profile_tts_base_url() == ""
