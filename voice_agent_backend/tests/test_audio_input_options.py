from agent.pipeline.audio_input_options import resolve_audio_input_options


def test_audio_frame_size_from_env(monkeypatch):
    monkeypatch.setenv("VOICE_AGENT_AUDIO_FRAME_SIZE_MS", "20")
    opts = resolve_audio_input_options(sample_rate=16000)
    assert opts.frame_size_ms == 20
    assert opts.sample_rate == 16000
    assert opts.pre_connect_audio is True


def test_audio_frame_size_clamped(monkeypatch):
    monkeypatch.setenv("VOICE_AGENT_AUDIO_FRAME_SIZE_MS", "5")
    opts = resolve_audio_input_options(sample_rate=16000)
    assert opts.frame_size_ms == 10
