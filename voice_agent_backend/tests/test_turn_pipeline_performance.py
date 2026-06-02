"""TurnPipelineTracker performance summary tests."""

from agent.observability.pipeline_latency import TurnPipelineTracker


def test_build_summary_computes_segment_durations():
    t = TurnPipelineTracker(room="r1")
    t.configure_providers(stt_provider="faster_whisper", tts_provider="supertonic")
    t.mark_user_speech_start()
    t.mark_user_speech_end()
    t.mark_stt_processing_start()
    t.mark_stt_first_partial(text_preview="hel")
    t.mark_stt_final(transcript_preview="hello")
    t.reset_turn(room="r1", phase="llm")
    t.mark_llm_dispatch()
    t.mark_llm_first_token(token_preview="Hi")
    t.mark_llm_complete(response_length=5)
    t.mark_tts_http_start(text_chars=5, mode="test")
    t.mark_tts_first_server_chunk()
    t.mark_tts_http_complete(timing_ms=120, bytes_received=8000)
    t.mark_tts_first_playout_chunk(chunk_bytes=4096)
    t.mark_playback_complete()

    summary = t.build_summary()
    assert summary["stt_post_speech_ms"] is not None
    assert summary["llm_time_to_first_token_ms"] is not None
    assert summary["llm_total_ms"] is not None
    assert summary["tts_http_total_ms"] == 120
    assert "e2e_to_first_audio_ms" in summary
    assert summary.get("e2e_to_playback_complete_ms") is not None
    assert "total_turn_latency_ms" not in summary
    assert summary["transcript_length"] == 5
    assert summary["assistant_response_length"] == 5


def test_begin_pipeline_turn_assigns_unique_ids():
    t = TurnPipelineTracker(room="r1")
    a = t.begin_pipeline_turn(turn_seq=1)
    b = t.begin_pipeline_turn(turn_seq=2)
    assert a != b
    assert t.turn_seq == 2


def test_begin_pipeline_turn_clears_stt_speech_clocks():
    t = TurnPipelineTracker(room="r1")
    t.mark_user_speech_start()
    t.mark_user_speech_end()
    t.begin_pipeline_turn(turn_seq=1)
    assert t.t_user_speech_start is None
    assert t.t_user_speech_end is None


def test_reset_turn_llm_phase_preserves_turn_id():
    t = TurnPipelineTracker(room="r1")
    t.begin_pipeline_turn(turn_seq=1)
    before = t.turn_id
    t.reset_turn(room="r1", phase="llm")
    assert t.turn_id == before


def test_reset_turn_llm_phase_snapshots_stt_and_clears_live():
    t = TurnPipelineTracker(room="r1")
    t.mark_user_speech_start()
    t.mark_stt_final(transcript_preview="hello there")
    t.reset_turn(room="r1", phase="llm")
    assert t._snap_stt_final is not None
    assert t._snap_transcript_length == 11
    assert t.t_user_speech_start is None
    assert t.t_stt_final is None
    assert t.t_llm_dispatch is None
    assert t.t_llm_phase_start is not None


def test_second_utterance_gets_fresh_stt_final_log(caplog):
    import logging

    caplog.set_level(logging.INFO, logger="agent.observability.pipeline_events")
    t = TurnPipelineTracker(room="r1")
    t.mark_user_speech_start()
    t.mark_stt_final(transcript_preview="first")
    t.reset_turn(room="r1", phase="llm")
    t.mark_user_speech_start()
    t.mark_stt_final(transcript_preview="second")
    finals = [
        r for r in caplog.records if "event=STT_FINAL " in r.message
    ]
    assert len(finals) == 2


def test_tts_http_total_accumulates():
    t = TurnPipelineTracker(room="r1")
    t.reset_turn(room="r1", phase="llm")
    t.mark_tts_http_start(text_chars=10, mode="test")
    t.mark_tts_http_complete(timing_ms=100, bytes_received=1000)
    t.mark_tts_http_start(text_chars=20, mode="test")
    t.mark_tts_http_complete(timing_ms=80, bytes_received=2000)
    assert t.tts_http_total_ms == 180
    assert t.tts_bytes_received == 3000


def test_mark_methods_are_idempotent():
    t = TurnPipelineTracker(room="r1")
    t.mark_user_speech_start()
    t.mark_user_speech_start()
    t.reset_turn(room="r1", phase="llm")
    t.mark_llm_dispatch()
    t.mark_llm_dispatch()
    assert t.t_llm_dispatch is not None


def test_mark_stt_final_and_llm_complete_log_once(caplog):
    import logging

    caplog.set_level(
        logging.INFO, logger="agent.observability.pipeline_events"
    )
    t = TurnPipelineTracker(room="r1")
    t.mark_stt_final(transcript_preview="hello")
    t.mark_stt_final(transcript_preview="hello again")
    stt_final_lines = [
        r for r in caplog.records if "event=STT_FINAL " in r.message
    ]
    assert len(stt_final_lines) == 1

    caplog.clear()
    t.reset_turn(room="r1", phase="llm")
    t.mark_llm_dispatch()
    t.mark_llm_complete(response_length=3)
    t.mark_llm_complete(response_length=10)
    llm_done_lines = [
        r for r in caplog.records if "event=LLM_RESPONSE_COMPLETE" in r.message
    ]
    assert len(llm_done_lines) == 1
    assert t.assistant_response_length == 10


def test_end_greeting_phase_clears_stt():
    t = TurnPipelineTracker(room="r1")
    t.mark_user_speech_start()
    t.mark_stt_processing_start()
    t.end_greeting_phase()
    assert t.t_user_speech_start is None
    assert t._snap_stt_final is None
