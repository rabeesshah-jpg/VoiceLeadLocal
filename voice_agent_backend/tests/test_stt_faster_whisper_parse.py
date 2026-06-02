from agent.pipeline.stt_faster_whisper import (
    _WlkStreamState,
    _parse_server_text,
    _parse_wlk_message,
    _utterance_final_text,
)


def test_utterance_final_prefers_committed_plus_buffer():
    state = _WlkStreamState(
        committed_texts=["Hey, how are you"],
        last_buffer="doing",
    )
    assert _utterance_final_text(state) == "Hey, how are you doing"


def test_parse_wlk_buffer_partial_only():
    state = _WlkStreamState()
    events = _parse_wlk_message(
        {
            "status": "active_transcription",
            "lines": [{"speaker": 1, "text": "Hello", "start": "0:00:00", "end": "0:00:01"}],
            "buffer_transcription": "world",
        },
        state,
    )
    assert len(events) == 1
    assert events[0]["text"] == "Hello world"
    assert events[0]["is_partial"] is True
    assert state.committed_texts == ["Hello"]


def test_parse_wlk_no_per_line_finals():
    state = _WlkStreamState()
    events = _parse_wlk_message(
        {
            "status": "active_transcription",
            "lines": [{"speaker": 1, "text": "tell", "start": "0:00:00", "end": "0:00:01"}],
            "buffer_transcription": "",
        },
        state,
    )
    assert len(events) == 1
    assert events[0]["text"] == "tell"
    assert events[0]["is_partial"] is True


def test_parse_wlk_lines_only_without_buffer():
    state = _WlkStreamState()
    events = _parse_wlk_message(
        {
            "status": "active_transcription",
            "lines": [
                {"speaker": 1, "text": "Hello", "start": "0:00:00", "end": "0:00:01"},
                {"speaker": 1, "text": "world", "start": "0:00:01", "end": "0:00:02"},
            ],
        },
        state,
    )
    assert len(events) == 1
    assert events[0]["text"] == "Hello world"


def test_ready_to_stop_emits_single_final():
    state = _WlkStreamState()
    state.committed_texts = ["My name is", "Ali"]
    state.last_buffer = ""
    events = _parse_wlk_message({"type": "ready_to_stop"}, state)
    assert len(events) == 1
    assert events[0]["is_final"] is True
    assert events[0]["text"] == "My name is Ali"


def test_utterance_final_merges_buffer():
    state = _WlkStreamState(committed_texts=["Tell me"], last_buffer="about yourself")
    assert _utterance_final_text(state) == "Tell me about yourself"


def test_parse_ready_to_stop_flag():
    state = _WlkStreamState()
    events = _parse_server_text('{"type": "ready_to_stop"}', state)
    assert state.ready_to_stop is True
