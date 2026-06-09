from apps.calls.services.tts_voice_clone import parse_runpod_voice_response


def test_ready_with_tts_usable_false_not_ready():
    parsed = parse_runpod_voice_response(
        {
            "voice_id": "uuid-1",
            "status": "ready",
            "metadata": {
                "engine_voice_name": "vp_1",
                "cloning_supported": False,
            },
        }
    )
    assert parsed["status"] == "ready"
    assert parsed["provider_voice_id"] == ""
    assert parsed["tts_usable"] is False


def test_ready_uses_uuid_not_engine_name():
    parsed = parse_runpod_voice_response(
        {
            "voice_id": "uuid-1",
            "status": "ready",
            "metadata": {
                "engine_voice_name": "vp_1",
                "cloning_supported": True,
            },
        }
    )
    assert parsed["provider_voice_id"] == "uuid-1"
    assert parsed["engine_voice_name"] == "vp_1"
