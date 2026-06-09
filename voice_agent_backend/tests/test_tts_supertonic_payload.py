import pytest
from livekit.agents import APIStatusError

from agent.pipeline.tts_supertonic import _TTSOptions, _build_payload


def test_custom_payload_uses_voice_id_only():
    opts = _TTSOptions(
        base_url="http://example:34679",
        voice="M1",
        lang="en",
        model="supertonic-3",
        response_format="wav",
        max_chunk_length=None,
        min_text_chars=8,
        max_text_chars=2000,
        provider_voice_id="db1db1aa-bb3e-40d5-b2f0-c28179a3dcdb",
        fallback_voice="M1",
        voice_profile_id="local-id",
        voice_mode="custom",
    )
    payload = _build_payload(opts, "Hello")
    assert payload == {
        "text": "Hello",
        "lang": "en",
        "response_format": "wav",
        "voice_id": "db1db1aa-bb3e-40d5-b2f0-c28179a3dcdb",
    }


def test_preset_payload_uses_voice_only():
    opts = _TTSOptions(
        base_url="http://example:34679",
        voice="M1",
        lang="en",
        model="supertonic-3",
        response_format="wav",
        max_chunk_length=None,
        min_text_chars=8,
        max_text_chars=2000,
        voice_mode="preset",
    )
    payload = _build_payload(opts, "Hello")
    assert payload == {
        "text": "Hello",
        "voice": "M1",
        "lang": "en",
        "response_format": "wav",
    }


def test_custom_without_provider_voice_id_raises():
    opts = _TTSOptions(
        base_url="http://example:34679",
        voice="M1",
        lang="en",
        model="supertonic-3",
        response_format="wav",
        max_chunk_length=None,
        min_text_chars=8,
        max_text_chars=2000,
        voice_mode="custom",
        voice_profile_id="local-id",
    )
    with pytest.raises(APIStatusError):
        _build_payload(opts, "Hello")
