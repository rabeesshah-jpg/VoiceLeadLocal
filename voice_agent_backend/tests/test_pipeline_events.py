from agent.observability.pipeline_events import (
    log_pipeline_event,
    tts_stream_mode_label,
)


def test_tts_stream_mode_label_phrase(monkeypatch):
    monkeypatch.setenv("VOICE_AGENT_TTS_STREAM_PHRASES", "true")
    assert tts_stream_mode_label() == "phrase_post_per_sentence"


def test_tts_stream_mode_label_buffered(monkeypatch):
    monkeypatch.setenv("VOICE_AGENT_TTS_STREAM_PHRASES", "false")
    assert tts_stream_mode_label() == "single_post_after_llm_buffered"


def test_log_pipeline_event_emits(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="agent.observability.pipeline_events")
    log_pipeline_event(
        "USER_FINAL_TRANSCRIPT",
        room="test-room",
        turn_id="abc",
        text_preview="hello",
    )
    log_pipeline_event(
        "LLM_START",
        room="test-room",
        turn_id="abc",
    )
    assert any("PIPELINE_EVENT event=USER_FINAL_TRANSCRIPT" in r.message for r in caplog.records)
    assert any(
        "PIPELINE_EVENT event=LLM_START" in r.message and "delta_ms=" in r.message
        for r in caplog.records
    )
