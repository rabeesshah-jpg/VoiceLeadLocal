import pytest

from agent.pipeline.stt_config import (
    get_stt_provider,
    is_deepgram_provider,
    is_faster_whisper_provider,
    resolve_faster_whisper_language,
    resolve_faster_whisper_ws_url,
    validate_stt_env,
)


def test_faster_whisper_provider_aliases():
    for name in ("faster_whisper", "faster-whisper", "wlk", "whisper", "whisperlivekit"):
        assert is_faster_whisper_provider(name)
    assert is_deepgram_provider("deepgram")
    assert not is_deepgram_provider("wlk")


def test_resolve_ws_url_from_explicit_stt_ws(monkeypatch):
    monkeypatch.setenv("STT_WS_URL", "ws://172.16.2.158:8000/asr")
    monkeypatch.setenv("STT_LANGUAGE", "en")
    monkeypatch.delenv("STT_BASE_URL", raising=False)
    url = resolve_faster_whisper_ws_url("en")
    assert url.startswith("ws://172.16.2.158:8000/asr")
    assert "language=en" in url


def test_resolve_ws_url_from_http_base(monkeypatch):
    monkeypatch.delenv("STT_WS_URL", raising=False)
    monkeypatch.setenv("STT_BASE_URL", "http://172.16.2.158:8000")
    monkeypatch.setenv("STT_LANGUAGE", "en")
    url = resolve_faster_whisper_ws_url("en")
    assert url.startswith("ws://172.16.2.158:8000/asr")
    assert "language=en" in url


def test_validate_stt_env_deepgram_requires_key(monkeypatch, settings):
    settings.STT_PROVIDER = "deepgram"
    settings.DEEPGRAM_API_KEY = ""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    assert "DEEPGRAM_API_KEY" in validate_stt_env()


def test_validate_stt_env_faster_whisper_skips_deepgram(monkeypatch, settings):
    settings.STT_PROVIDER = "wlk"
    settings.STT_WS_URL = "ws://127.0.0.1:8000/asr"
    settings.DEEPGRAM_API_KEY = ""
    monkeypatch.delenv("DEEPGRAM_API_KEY", raising=False)
    assert "DEEPGRAM_API_KEY" not in validate_stt_env()


def test_resolve_language_auto_uses_conversation_lang(monkeypatch):
    monkeypatch.setenv("STT_PROVIDER", "faster_whisper")
    monkeypatch.setenv("FASTER_WHISPER_LANGUAGE", "auto")
    assert resolve_faster_whisper_language("ar") == "ar"


def test_stt_provider_env_overrides_legacy_when_unset_in_django(monkeypatch, settings):
    settings.STT_PROVIDER = ""
    settings.VOICE_AGENT_STT_PROVIDER = ""
    monkeypatch.setenv("STT_PROVIDER", "wlk")
    monkeypatch.setenv("VOICE_AGENT_STT_PROVIDER", "deepgram")
    assert get_stt_provider() == "wlk"
