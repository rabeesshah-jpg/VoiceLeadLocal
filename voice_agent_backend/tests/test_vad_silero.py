from agent.pipeline.vad_silero import (
    resolve_vad_activation_threshold,
    resolve_vad_min_silence_s,
    resolve_vad_min_speech_s,
)


def test_vad_min_silence_from_seconds_env(monkeypatch):
    monkeypatch.setenv("VOICE_AGENT_VAD_MIN_SILENCE_S", "0.35")
    monkeypatch.delenv("VAD_SILENCE_TIMEOUT_MS", raising=False)
    assert resolve_vad_min_silence_s() == 0.35


def test_vad_min_silence_from_legacy_ms_env(monkeypatch):
    monkeypatch.delenv("VOICE_AGENT_VAD_MIN_SILENCE_S", raising=False)
    monkeypatch.setenv("VAD_SILENCE_TIMEOUT_MS", "300")
    assert resolve_vad_min_silence_s() == 0.3


def test_vad_min_silence_default(monkeypatch):
    monkeypatch.delenv("VOICE_AGENT_VAD_MIN_SILENCE_S", raising=False)
    monkeypatch.delenv("VAD_SILENCE_TIMEOUT_MS", raising=False)
    assert resolve_vad_min_silence_s() == 0.4


def test_vad_min_speech_env(monkeypatch):
    monkeypatch.setenv("VOICE_AGENT_VAD_MIN_SPEECH_S", "0.03")
    assert resolve_vad_min_speech_s() == 0.03


def test_vad_activation_threshold_clamped(monkeypatch):
    monkeypatch.setenv("VOICE_AGENT_VAD_ACTIVATION_THRESHOLD", "1.5")
    assert resolve_vad_activation_threshold() == 1.0
