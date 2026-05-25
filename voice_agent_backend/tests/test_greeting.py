from agent.greeting import get_call_greeting


def test_greeting_english():
    text = get_call_greeting("en")
    assert "Noura" in text
    assert "Good Websites" in text


def test_greeting_arabic():
    text = get_call_greeting("ar")
    assert "نورة" in text
    assert "Good Websites" in text
