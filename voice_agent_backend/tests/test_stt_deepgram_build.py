"""Deepgram STT builder env and defaults."""

from __future__ import annotations

import pytest

from agent.pipeline import stt_deepgram


@pytest.fixture
def capture_stt(monkeypatch):
    captured: dict = {}

    class _FakeSTT:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(stt_deepgram.deepgram, "STT", _FakeSTT)
    return captured


def test_build_deepgram_stt_defaults(capture_stt, monkeypatch):
    monkeypatch.delenv("VOICE_AGENT_DEEPGRAM_ENDPOINTING_MS", raising=False)
    monkeypatch.delenv("VOICE_AGENT_DEEPGRAM_SMART_FORMAT", raising=False)
    monkeypatch.delenv("VOICE_AGENT_DEEPGRAM_FILLER_WORDS", raising=False)
    monkeypatch.delenv("VOICE_AGENT_DEEPGRAM_PUNCTUATE", raising=False)
    monkeypatch.delenv("VOICE_AGENT_DEEPGRAM_BASE_URL", raising=False)
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")

    stt_deepgram.build_deepgram_stt("en")

    assert capture_stt["endpointing_ms"] == 100
    assert capture_stt["smart_format"] is False
    assert capture_stt["filler_words"] is False
    assert capture_stt["punctuate"] is True
    assert capture_stt["no_delay"] is True


def test_build_deepgram_stt_env_overrides(capture_stt, monkeypatch):
    monkeypatch.setenv("DEEPGRAM_API_KEY", "test-key")
    monkeypatch.setenv("VOICE_AGENT_DEEPGRAM_ENDPOINTING_MS", "75")
    monkeypatch.setenv("VOICE_AGENT_DEEPGRAM_SMART_FORMAT", "true")
    monkeypatch.setenv("VOICE_AGENT_DEEPGRAM_FILLER_WORDS", "true")
    monkeypatch.setenv("VOICE_AGENT_DEEPGRAM_BASE_URL", "https://api.eu.deepgram.com/v1/listen")

    stt_deepgram.build_deepgram_stt("en")

    assert capture_stt["endpointing_ms"] == 75
    assert capture_stt["smart_format"] is True
    assert capture_stt["filler_words"] is True
    assert capture_stt["base_url"] == "https://api.eu.deepgram.com/v1/listen"
