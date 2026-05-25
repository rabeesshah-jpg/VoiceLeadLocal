import pytest

from agent.pipeline.stt_transcript_buffer import joined_transcript, merge_stt_segment


def test_merge_appends_new_segment():
    parts = merge_stt_segment([], "Hello")
    parts = merge_stt_segment(parts, "world today")
    assert joined_transcript(parts) == "Hello world today"


def test_merge_replaces_with_longer_cumulative():
    parts = merge_stt_segment([], "Hello")
    parts = merge_stt_segment(parts, "Hello world")
    assert parts == ["Hello world"]


def test_joined_includes_interim_tail():
    parts = ["Hello"]
    assert joined_transcript(parts, interim="Hello wor") == "Hello wor"
