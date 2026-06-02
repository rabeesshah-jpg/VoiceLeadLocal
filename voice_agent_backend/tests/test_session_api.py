import pytest
from django.test import Client


@pytest.fixture
def api_client(settings):
    settings.VOICE_AGENT_DEV_API_KEY = "test-api-key"
    settings.LIVEKIT_URL = "wss://test.livekit.cloud"
    settings.LIVEKIT_API_KEY = "APItest"
    settings.LIVEKIT_API_SECRET = "secret_test_key_32chars_minimum_xx"
    settings.STT_PROVIDER = "faster_whisper"
    settings.STT_WS_URL = "ws://127.0.0.1:8000/asr"
    settings.DEEPGRAM_API_KEY = ""
    settings.OPENROUTER_API_KEY = "or-test"
    settings.TTS_BASE_URL = "http://supertonic-test.example:7788"
    client = Client()
    client.defaults["HTTP_X_VOICE_AGENT_DEV_KEY"] = "test-api-key"
    return client


@pytest.mark.django_db
def test_health(api_client):
    resp = api_client.get("/api/health/")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


@pytest.mark.django_db
def test_start_and_end_call(api_client):
    start = api_client.post(
        "/api/calls/start/",
        data={"system_prompt": "You are a test bot."},
        content_type="application/json",
    )
    assert start.status_code == 201
    body = start.json()
    assert body["call_id"]
    assert body["participant_token"]
    assert body["room_name"].startswith("va-")

    detail = api_client.get(f"/api/calls/{body['call_id']}/")
    assert detail.status_code == 200
    assert detail.json()["status"] == "active"

    end = api_client.post(
        f"/api/calls/{body['call_id']}/end/",
        data={"reason": "test"},
        content_type="application/json",
    )
    assert end.status_code == 200
    assert end.json()["status"] == "ended"


@pytest.mark.django_db
def test_start_call_with_arabic_language(api_client):
    start = api_client.post(
        "/api/calls/start/",
        data={"language": "ar", "persona_id": "male"},
        content_type="application/json",
    )
    assert start.status_code == 201
    call_id = start.json()["call_id"]
    detail = api_client.get(f"/api/calls/{call_id}/")
    assert detail.status_code == 200
    assert detail.json()["language"] == "ar"


@pytest.mark.django_db
def test_start_call_rejects_invalid_language(api_client):
    resp = api_client.post(
        "/api/calls/start/",
        data={"language": "fr"},
        content_type="application/json",
    )
    assert resp.status_code == 400


@pytest.mark.django_db
def test_start_call_faster_whisper_without_deepgram(monkeypatch, settings):
    monkeypatch.setenv("STT_PROVIDER", "wlk")
    monkeypatch.setenv("STT_WS_URL", "ws://127.0.0.1:8000/asr")
    settings.STT_PROVIDER = "wlk"
    settings.DEEPGRAM_API_KEY = ""
    client = Client()
    client.defaults["HTTP_X_VOICE_AGENT_DEV_KEY"] = "test-api-key"
    settings.LIVEKIT_URL = "wss://test.livekit.cloud"
    settings.LIVEKIT_API_KEY = "APItest"
    settings.LIVEKIT_API_SECRET = "secret_test_key_32chars_minimum_xx"
    settings.OPENROUTER_API_KEY = "or-test"
    settings.TTS_BASE_URL = "http://supertonic-test.example:7788"
    resp = client.post(
        "/api/calls/start/",
        data={"language": "en"},
        content_type="application/json",
    )
    assert resp.status_code == 201


@pytest.mark.django_db
def test_start_requires_api_key(settings):
    client = Client()
    settings.LIVEKIT_URL = "wss://test.livekit.cloud"
    settings.LIVEKIT_API_KEY = "APItest"
    settings.LIVEKIT_API_SECRET = "secret_test_key_32chars_minimum_xx"
    settings.STT_PROVIDER = "faster_whisper"
    settings.STT_WS_URL = "ws://127.0.0.1:8000/asr"
    settings.DEEPGRAM_API_KEY = ""
    settings.OPENROUTER_API_KEY = "or-test"
    settings.TTS_BASE_URL = "http://supertonic-test.example:7788"
    resp = client.post("/api/calls/start/", data={}, content_type="application/json")
    assert resp.status_code == 403
