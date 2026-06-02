"""STT latency metrics and summary logging."""

import logging

import pytest

from agent.observability.pipeline_latency import TurnPipelineTracker


def test_build_stt_metrics_includes_missing_reasons():
    t = TurnPipelineTracker(room="r1")
    t.configure_providers(stt_provider="deepgram")
    t.begin_pipeline_turn(turn_seq=1)
    t.mark_user_speech_start()
    t.mark_stt_final(transcript_preview="hi")
    t._snapshot_stt_phase()

    m = t.build_stt_metrics()
    assert m["stt_first_partial_ms"] is None
    assert m["stt_first_partial_ms_missing_reason"] == "first_partial_not_seen"
    assert m["stt_speech_duration_ms_missing_reason"] == "speech_end_not_seen"
    assert m["stt_speech_end_to_final_ms_missing_reason"] == "speech_end_not_seen"
    assert m["stt_wall_ms"] is not None
    assert m["deepgram_endpointing_ms"] is not None


def test_stt_first_partial_ms_from_speech_start():
    t = TurnPipelineTracker(room="r1")
    t.begin_pipeline_turn(turn_seq=1)
    t.mark_user_speech_start()
    t.record_stt_partial(text_preview="hel")
    t.mark_user_speech_end()
    t.mark_stt_final(transcript_preview="hello")
    t.reset_turn(room="r1", phase="llm")

    m = t.build_stt_metrics()
    assert m["stt_first_partial_ms"] is not None
    assert m["stt_speech_duration_ms"] is not None
    assert m["stt_partial_count"] == 1


def test_mark_transcript_rejected_logs_summary(caplog):
    caplog.set_level(logging.INFO, logger="agent.observability.pipeline_events")
    t = TurnPipelineTracker(room="r1")
    t.begin_pipeline_turn(turn_seq=2)
    t.mark_user_speech_start()
    t.mark_user_speech_end()
    t.mark_transcript_rejected(reason="too_short", transcript_preview="No")

    summaries = [r for r in caplog.records if "event=STT_LATENCY_SUMMARY" in r.message]
    assert len(summaries) == 1
    assert "stt_rejected=True" in summaries[0].message or "low_confidence" in summaries[0].message


def test_pipeline_perf_includes_stt_fields_with_nulls(caplog):
    caplog.set_level(logging.INFO, logger="agent.observability.pipeline_latency")
    t = TurnPipelineTracker(room="r1")
    t.configure_providers(stt_provider="deepgram", tts_provider="supertonic")
    t.begin_pipeline_turn(turn_seq=1)
    t.mark_user_speech_start()
    t.mark_stt_final(transcript_preview="hello")
    t.reset_turn(room="r1", phase="llm")
    t.mark_llm_dispatch()
    t.mark_playback_complete()

    perf_lines = [r for r in caplog.records if "PIPELINE_PERF" in r.message]
    assert perf_lines
    body = perf_lines[-1].message
    assert "stt provider" in body.lower() or "stt_provider" in body
    assert "null" in body.lower()


def test_stt_partial_count_accumulates():
    t = TurnPipelineTracker(room="r1")
    t.begin_pipeline_turn(turn_seq=1)
    t.mark_user_speech_start()
    t.record_stt_partial(text_preview="a")
    t.record_stt_partial(text_preview="ab")
    t.record_stt_partial(text_preview="abc")
    assert t.stt_partial_count == 3


def test_begin_turn_preserves_existing_speech_start():
    t = TurnPipelineTracker(room="r1")
    t.mark_user_speech_start()
    ts = t.t_user_speech_start
    t.begin_pipeline_turn(turn_seq=1)
    assert t.t_user_speech_start == ts


def test_missing_final_reports_stt_final_not_seen():
    t = TurnPipelineTracker(room="r1")
    t.begin_pipeline_turn(turn_seq=1)
    t.mark_user_speech_start()
    t.mark_user_speech_end()
    t._snapshot_stt_phase()
    m = t.build_stt_metrics()
    assert m["stt_speech_end_to_final_ms"] is None
    assert m["stt_speech_end_to_final_ms_missing_reason"] == "stt_final_not_seen"
    assert m["stt_wall_ms_missing_reason"] == "stt_final_not_seen"


def test_emit_stt_summary_after_final(caplog):
    caplog.set_level(logging.INFO, logger="agent.observability.pipeline_events")
    t = TurnPipelineTracker(room="r1")
    t.begin_pipeline_turn(turn_seq=1)
    t.mark_user_speech_start()
    t.mark_user_speech_end()
    t.mark_stt_final(transcript_preview="hello")
    t.emit_stt_latency_summary()
    summaries = [r for r in caplog.records if "event=STT_LATENCY_SUMMARY" in r.message]
    assert len(summaries) == 1
