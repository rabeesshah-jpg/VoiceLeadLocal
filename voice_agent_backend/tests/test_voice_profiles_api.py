import io
import json
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.calls.models import VoiceProfile


@pytest.fixture
def api_client(settings):
    settings.VOICE_AGENT_DEV_API_KEY = "test-api-key"
    settings.TTS_VOICE_CLONING_ENABLED = True
    settings.TTS_BASE_URL = "http://supertonic-test.example:7788"
    client = __import__("django.test", fromlist=["Client"]).Client()
    client.defaults["HTTP_X_VOICE_AGENT_DEV_KEY"] = "test-api-key"
    return client


def _caps_audio_only():
    return {
        "voice_cloning_enabled": True,
        "supports_reference_audio_cloning": True,
        "supports_voice_builder_json": True,
        "message": "",
    }


def _caps_json_only():
    return {
        "voice_cloning_enabled": True,
        "supports_reference_audio_cloning": False,
        "supports_voice_builder_json": True,
        "message": (
            "Recording-based cloning is not enabled on this server. "
            "Upload a Voice Builder JSON file instead."
        ),
    }


@pytest.mark.django_db
@patch("apps.calls.voice_profile_views.get_voice_clone_capabilities")
def test_list_voice_profiles_empty(mock_caps, api_client):
    mock_caps.return_value = _caps_json_only()
    resp = api_client.get("/api/voice-profiles/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["voice_cloning_enabled"] is True
    assert body["supports_voice_builder_json"] is True
    assert "tts_base_url" not in body
    assert "103.196.86.102" not in json.dumps(body)
    assert body["profiles"] == []


@pytest.mark.django_db
@patch("apps.calls.voice_profile_views.get_voice_clone_capabilities")
def test_clone_rejects_when_audio_unsupported(mock_caps, api_client):
    mock_caps.return_value = _caps_json_only()
    audio = SimpleUploadedFile(
        "sample.webm",
        b"fake-webm-audio",
        content_type="audio/webm",
    )
    resp = api_client.post(
        "/api/voice-profiles/clone/",
        data={
            "name": "My Voice",
            "audio": audio,
            "consent_confirmed": "true",
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    assert "Voice Builder JSON" in body["error"]
    assert VoiceProfile.objects.count() == 0


@pytest.mark.django_db
@patch("apps.calls.voice_profile_views.get_voice_clone_capabilities")
@patch("apps.calls.services.voice_profile_service.get_voice_clone_capabilities")
@patch("apps.calls.services.voice_profile_service.clone_voice_on_runpod")
def test_clone_voice_profile(mock_clone, mock_caps_service, mock_caps_view, api_client):
    mock_caps_view.return_value = _caps_audio_only()
    mock_caps_service.return_value = _caps_audio_only()
    mock_clone.return_value = {
        "provider_voice_id": "uuid-123",
        "runpod_voice_uuid": "uuid-123",
        "status": "ready",
        "status_message": "",
        "raw": {},
    }
    audio = SimpleUploadedFile(
        "sample.webm",
        b"fake-webm-audio",
        content_type="audio/webm",
    )
    resp = api_client.post(
        "/api/voice-profiles/clone/",
        data={
            "name": "My Voice",
            "audio": audio,
            "consent_confirmed": "true",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "My Voice"
    assert body["status"] == "ready"
    assert body["provider_voice_id"] == "uuid-123"
    assert body["source_type"] == "reference_audio"
    assert VoiceProfile.objects.count() == 1


@pytest.mark.django_db
@patch("apps.calls.voice_profile_views.get_voice_clone_capabilities")
def test_clone_rejects_missing_audio(mock_caps, api_client):
    mock_caps.return_value = _caps_audio_only()
    resp = api_client.post(
        "/api/voice-profiles/clone/",
        data={"name": "No Audio", "consent_confirmed": "true"},
    )
    assert resp.status_code == 400


@pytest.mark.django_db
@patch("apps.calls.voice_profile_views.get_voice_clone_capabilities")
@patch("apps.calls.services.voice_profile_service.get_voice_clone_capabilities")
@patch("apps.calls.services.voice_profile_service.upload_voice_builder_json_on_runpod")
def test_upload_json_voice_profile_success(
    mock_upload, mock_caps_service, mock_caps_view, api_client
):
    mock_caps_view.return_value = _caps_json_only()
    mock_caps_service.return_value = _caps_json_only()
    mock_upload.return_value = {
        "provider_voice_id": "uuid-json",
        "runpod_voice_uuid": "uuid-json",
        "status": "ready",
        "status_message": "ready",
        "raw": {},
    }
    payload = json.dumps({"style": "test"}).encode()
    upload = SimpleUploadedFile(
        "myvoice.json",
        payload,
        content_type="application/json",
    )
    resp = api_client.post(
        "/api/voice-profiles/upload-json/",
        data={
            "file": upload,
            "display_name": "Ali JSON",
            "consent_confirmed": "true",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "ready"
    assert body["provider_voice_id"] == "uuid-json"
    assert body["source_type"] == "voice_builder_json"
    profile = VoiceProfile.objects.get(pk=body["id"])
    assert profile.runpod_voice_uuid == "uuid-json"


@pytest.mark.django_db
@patch("apps.calls.voice_profile_views.get_voice_clone_capabilities")
@patch("apps.calls.services.voice_profile_service.get_voice_clone_capabilities")
def test_upload_json_rejects_non_json(mock_caps_service, mock_caps_view, api_client):
    mock_caps_view.return_value = _caps_json_only()
    mock_caps_service.return_value = _caps_json_only()
    upload = SimpleUploadedFile(
        "bad.txt",
        b"not-json",
        content_type="text/plain",
    )
    resp = api_client.post(
        "/api/voice-profiles/upload-json/",
        data={
            "file": upload,
            "display_name": "Bad",
            "consent_confirmed": "true",
        },
    )
    assert resp.status_code == 400
    assert "json" in resp.json()["error"].lower()


@pytest.mark.django_db
@patch("apps.calls.voice_profile_views.get_voice_clone_capabilities")
@patch("apps.calls.services.voice_profile_service.get_voice_clone_capabilities")
@patch("apps.calls.services.voice_profile_service.upload_voice_builder_json_on_runpod")
def test_upload_json_stored_only_not_ready(
    mock_upload, mock_caps_service, mock_caps_view, api_client
):
    mock_caps_view.return_value = _caps_json_only()
    mock_caps_service.return_value = _caps_json_only()
    mock_upload.side_effect = RuntimeError("Voice JSON stored but not synthesizable (stored_only).")
    upload = SimpleUploadedFile(
        "myvoice.json",
        b"{}",
        content_type="application/json",
    )
    resp = api_client.post(
        "/api/voice-profiles/upload-json/",
        data={
            "file": upload,
            "display_name": "Bad JSON",
            "consent_confirmed": "true",
        },
    )
    assert resp.status_code == 502
    profile = VoiceProfile.objects.get(name="Bad JSON")
    assert profile.status == VoiceProfile.STATUS_FAILED
    assert profile.provider_voice_id == ""


@pytest.mark.django_db
@patch("apps.calls.services.voice_profile_service.synthesize_voice_test_on_runpod")
def test_test_voice_profile_returns_wav(mock_synth, api_client):
    mock_synth.return_value = b"RIFFfake-wav-data"
    profile = VoiceProfile.objects.create(
        name="Ready",
        status=VoiceProfile.STATUS_READY,
        provider_voice_id="vp_test",
        runpod_voice_uuid="uuid-test",
        source_type=VoiceProfile.SOURCE_VOICE_BUILDER_JSON,
    )
    resp = api_client.post(f"/api/voice-profiles/{profile.id}/test/")
    assert resp.status_code == 200
    assert resp["Content-Type"] == "audio/wav"
    assert resp.content == b"RIFFfake-wav-data"
    mock_synth.assert_called_once_with(provider_voice_id="vp_test")


@pytest.mark.django_db
@patch("apps.calls.services.voice_profile_service.fetch_voice_status_on_runpod")
def test_refresh_voice_profile(mock_fetch, api_client):
    mock_fetch.return_value = {
        "runpod_voice_uuid": "uuid-1",
        "provider_voice_id": "uuid-1",
        "status": "ready",
        "status_message": "",
    }
    profile = VoiceProfile.objects.create(
        name="Processing",
        status=VoiceProfile.STATUS_PROCESSING,
        runpod_voice_uuid="uuid-1",
    )
    resp = api_client.post(f"/api/voice-profiles/{profile.id}/refresh/")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["provider_voice_id"] == "uuid-1"


@pytest.mark.django_db
@patch("apps.calls.services.voice_profile_service.delete_voice_on_runpod")
def test_delete_voice_profile(mock_delete, api_client):
    profile = VoiceProfile.objects.create(
        name="Test",
        status=VoiceProfile.STATUS_READY,
        provider_voice_id="vp_x",
        runpod_voice_uuid="uuid-x",
    )
    resp = api_client.delete(f"/api/voice-profiles/{profile.id}/")
    assert resp.status_code == 200
    assert not VoiceProfile.objects.filter(pk=profile.id).exists()
    mock_delete.assert_called_once()


@pytest.mark.django_db
def test_start_call_with_voice_profile(api_client, settings):
    settings.LIVEKIT_URL = "wss://test.livekit.cloud"
    settings.LIVEKIT_API_KEY = "APItest"
    settings.LIVEKIT_API_SECRET = "secret_test_key_32chars_minimum_xx"
    settings.OPENROUTER_API_KEY = "or-test"
    settings.STT_PROVIDER = "faster_whisper"
    settings.STT_WS_URL = "ws://127.0.0.1:8000/asr"
    settings.DEEPGRAM_API_KEY = ""

    profile = VoiceProfile.objects.create(
        name="Clone",
        status=VoiceProfile.STATUS_READY,
        provider_voice_id="uuid-clone",
        runpod_voice_uuid="uuid-clone",
        source_type=VoiceProfile.SOURCE_VOICE_BUILDER_JSON,
    )

    settings.VOICE_AGENT_SKIP_ROOM_SETUP = False

    with patch("apps.calls.views.run_supertonic_handshake", return_value={"ok": True}), patch(
        "apps.calls.views.ensure_room_and_dispatch_agent",
        return_value={"dispatch_id": "d1"},
    ) as mock_dispatch, patch(
        "apps.calls.views.LiveKitTokenService"
    ) as mock_lk:
        mock_lk.return_value.validate_config.return_value = []
        mock_lk.return_value.mint_participant_token.return_value = type(
            "Tok",
            (),
            {
                "token": "jwt",
                "identity": "user-1",
                "livekit_url": "wss://x",
                "expires_at": __import__("datetime").datetime(
                    2026, 1, 1, tzinfo=__import__("datetime").timezone.utc
                ),
            },
        )()

        resp = api_client.post(
            "/api/calls/start/",
            data={
                "persona_id": "male",
                "language": "en",
                "voice_mode": "custom",
                "voice_profile_id": str(profile.id),
                "fallback_voice": "M1",
            },
            content_type="application/json",
        )

    assert resp.status_code == 201
    body = resp.json()
    from apps.calls.models import CallSession

    session = CallSession.objects.get(pk=body["call_id"])
    assert session.voice_mode == "custom"
    assert session.voice_profile_id == profile.id
    assert session.provider_voice_id == "uuid-clone"
    assert session.fallback_voice == "M1"
    assert body["voice"]["provider_voice_id"] == "uuid-clone"
    assert body["voice"]["tts_voice"] == "uuid-clone"
    mock_dispatch.assert_called_once()
    tts_config = mock_dispatch.call_args.kwargs.get("tts_config") or {}
    assert tts_config.get("provider_voice_id") == "uuid-clone"
    assert tts_config.get("voice_id") == "uuid-clone"


@pytest.mark.django_db
def test_start_call_rejects_not_ready_profile(api_client, settings):
    settings.LIVEKIT_URL = "wss://test.livekit.cloud"
    settings.LIVEKIT_API_KEY = "APItest"
    settings.LIVEKIT_API_SECRET = "secret_test_key_32chars_minimum_xx"
    settings.OPENROUTER_API_KEY = "or-test"
    settings.STT_PROVIDER = "faster_whisper"
    settings.STT_WS_URL = "ws://127.0.0.1:8000/asr"
    settings.DEEPGRAM_API_KEY = ""

    profile = VoiceProfile.objects.create(
        name="Pending",
        status=VoiceProfile.STATUS_PROCESSING,
    )

    resp = api_client.post(
        "/api/calls/start/",
        data={"voice_mode": "custom", "voice_profile_id": str(profile.id)},
        content_type="application/json",
    )
    assert resp.status_code == 400
