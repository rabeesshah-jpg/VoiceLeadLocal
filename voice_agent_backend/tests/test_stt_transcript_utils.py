from agent.pipeline.stt_transcript_utils import (
    clean_transcript,
    dedupe_consecutive_tokens,
    extract_stt_delta,
    is_duplicate_final_for_send,
    is_duplicate_transcript,
    transcript_extends_prior,
    validate_final_transcript,
)


def test_dedupe_consecutive_tokens():
    assert dedupe_consecutive_tokens("my my name name") == "my name"


def test_clean_transcript_strips_noise():
    assert clean_transcript("  hello   ( )  world  ") == "hello world"


def test_validate_rejects_garbled():
    ok, reason = validate_final_transcript("my name my name my full name")
    assert not ok
    assert reason == "garbled"


def test_validate_accepts_good_sentence():
    ok, reason = validate_final_transcript("My name is Ali")
    assert ok
    assert reason == "ok"


def test_validate_rejects_single_word_fragment():
    ok, _ = validate_final_transcript("tell")
    assert not ok


def test_is_duplicate_transcript():
    assert is_duplicate_transcript("hello world", "hello world")
    assert is_duplicate_transcript("hello", "hello world") is False


def test_is_duplicate_final_for_send():
    prior = "Hey, how are you? Tell me what you say."
    dup, reason = is_duplicate_final_for_send(
        delta=prior,
        last_sent_final=prior,
        last_committed_cumulative=prior,
    )
    assert dup
    assert reason == "already_sent"


def test_extract_stt_delta_suffix_only():
    prior = "Hey, how are you?"
    incoming = "Hey, how are you? Tell me what you say."
    assert extract_stt_delta(prior, incoming) == "Tell me what you say."


def test_transcript_extends_prior():
    assert transcript_extends_prior("will be about to self", "will be about to self My name")
    assert not transcript_extends_prior("hello world", "hello")
    assert not transcript_extends_prior("tell me", "tell")
