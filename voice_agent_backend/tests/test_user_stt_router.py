from agent.pipeline.user_stt_router import (
    default_user_stt_language,
    dominant_user_script,
    is_likely_mistranscribed_for_ar_call,
    resolve_user_stt_language_from_text,
)


def test_default_stt_language_arabic_call():
    assert default_user_stt_language("ar") == "en-US"


def test_default_stt_language_english_call():
    assert default_user_stt_language("en") == "en-US"


def test_dominant_script_latin():
    assert dominant_user_script("And how are you?") == "latin"


def test_dominant_script_arabic():
    assert dominant_user_script("مرحباً كيف حالك") == "arabic"


def test_dominant_script_devanagari_mistranscription():
    assert dominant_user_script("कई फ़ायदे") == "devanagari"


def test_resolve_stt_ar_call_english_speech():
    lang = resolve_user_stt_language_from_text(
        "And how are you?",
        conversation_language="ar",
        current_language="ar-SA",
    )
    assert lang == "en-US"


def test_resolve_stt_ar_call_arabic_speech():
    lang = resolve_user_stt_language_from_text(
        "مرحباً كيف حالك",
        conversation_language="ar",
        current_language="en-US",
    )
    assert lang == "ar-SA"


def test_resolve_stt_ar_call_devanagari_routes_to_arabic_stt():
    lang = resolve_user_stt_language_from_text(
        "कई सालों का",
        conversation_language="ar",
        current_language="en-US",
    )
    assert lang == "ar-SA"


def test_mistranscribed_hindi_detected():
    assert is_likely_mistranscribed_for_ar_call("कई फ़ायदे कई फ़्राहलों का")
