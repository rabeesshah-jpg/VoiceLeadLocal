import pytest

from agent.pipeline.tts_text_chunker import SentenceTextChunker, ends_sentence


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Hello.", True),
        ("Really?", True),
        ("Stop!", True),
        ('She said "yes."', True),
        ("Hello, world", False),
        ("One: two", False),
        ("Wait;", False),
    ],
)
def test_ends_sentence(text, expected):
    assert ends_sentence(text) is expected


def test_no_emit_on_comma_mid_clause():
    c = SentenceTextChunker(min_chars=5, max_chars=80)
    assert c.push("Hello, ") == []
    assert c.push("how are you") == []
    assert c.push(" today?") == ["Hello, how are you today?"]


def test_emit_on_period_not_comma():
    c = SentenceTextChunker(min_chars=3, max_chars=200)
    assert c.push("First part, second part.") == ["First part, second part."]
    assert c.push(" Next.") == ["Next."]


def test_no_mid_sentence_split_at_max_chars():
    c = SentenceTextChunker(min_chars=5, max_chars=40)
    long = "This is one long sentence without any period until the very end here"
    assert c.push(long) == []
    assert c.flush() == [long]


def test_overflow_splits_at_last_sentence_in_buffer():
    c = SentenceTextChunker(min_chars=5, max_chars=30)
    first = c.push("Done. " + "x" * 40)
    assert first == ["Done."]
    assert c.flush()[0].startswith("x")


def test_flush_emits_remainder_without_period():
    c = SentenceTextChunker(min_chars=5, max_chars=200)
    c.push("no terminal punct")
    assert c.flush() == ["no terminal punct"]
