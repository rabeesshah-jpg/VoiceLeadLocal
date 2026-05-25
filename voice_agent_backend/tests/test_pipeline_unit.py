import pytest

from agent.pipeline.interrupt import GenerationController
from agent.observability.latency import TtsCallTiming, TurnLatency
from agent.pipeline.llm_openrouter import get_voice_agent_instructions
from agent.prompts import (
    LANGUAGE_RULES,
    SUPERTONIC_EXPRESSION_TAGS,
    VOICE_AGENT_INSTRUCTIONS,
    get_voice_agent_instructions as build_instructions,
)
from agent.pipeline.stt_deepgram import resolve_deepgram_stt
from agent.pipeline.tts_multilingual_server import normalize_speaker_wav
from agent.pipeline.tts_factory import get_tts_provider
from agent.pipeline.voice_presets import resolve_tts_config


@pytest.mark.asyncio
async def test_generation_controller_bump():
    ctrl = GenerationController()
    gen1 = await ctrl.bump()
    assert ctrl.is_current(gen1)
    gen2 = await ctrl.bump()
    assert not ctrl.is_current(gen1)
    assert ctrl.is_current(gen2)


def test_turn_latency_payload():
    t = TurnLatency()
    t.mark_stt_final()
    t.mark_llm_first_token()
    payload = t.to_payload()
    assert "turn_id" in payload
    assert payload["stt_ms"] is None or isinstance(payload["stt_ms"], int)
    assert payload["llm_first_token_ms"] is None or payload["llm_first_token_ms"] >= 0


def test_turn_latency_llm_start_ms_from_stt_final():
    t = TurnLatency()
    t.mark_stt_final()
    t.mark_llm_dispatch()
    assert t.llm_start_ms() is not None
    assert t.llm_start_ms() >= 0
    payload = t.to_payload()
    assert payload["llm_start_ms"] == t.llm_start_ms()


def test_turn_latency_llm_start_omitted_when_dispatch_before_stt():
    t = TurnLatency()
    t.mark_llm_dispatch()
    t.mark_stt_final()
    assert t.llm_start_ms() is None


def test_turn_latency_llm_first_token_ms_from_stt_final():
    t = TurnLatency()
    t.mark_stt_final()
    t.mark_llm_dispatch()
    t.mark_llm_first_token()
    start_ms = t.llm_start_ms()
    token_ms = t.llm_first_token_ms()
    assert start_ms is not None
    assert token_ms is not None
    assert token_ms >= start_ms


def test_voice_agent_instructions_from_code():
    assert get_voice_agent_instructions() == VOICE_AGENT_INSTRUCTIONS
    assert LANGUAGE_RULES["en"] in VOICE_AGENT_INSTRUCTIONS
    assert "Speak only in English" in VOICE_AGENT_INSTRUCTIONS
    assert "only when it genuinely fits" in VOICE_AGENT_INSTRUCTIONS
    assert "Most replies should have no tag" in VOICE_AGENT_INSTRUCTIONS
    for tag in SUPERTONIC_EXPRESSION_TAGS:
        assert tag in VOICE_AGENT_INSTRUCTIONS


def test_voice_agent_instructions_arabic():
    ar = build_instructions("ar")
    assert LANGUAGE_RULES["ar"] in ar
    assert "Speak only in Arabic" in ar
    assert "مرحباً" in ar
    assert "press 1" not in ar.lower()


def test_deepgram_stt_arabic_call_defaults_to_en_us_then_routes():
    """Arabic calls start on en-US; entrypoint switches to ar-SA when caller speaks Arabic."""
    model, dg_lang = resolve_deepgram_stt("ar")
    assert model == "nova-3"
    assert dg_lang == "en-US"


def test_deepgram_stt_english_uses_nova3_en_us():
    model, dg_lang = resolve_deepgram_stt("en")
    assert model == "nova-3"
    assert dg_lang == "en-US"


def test_normalize_speaker_wav():
    assert normalize_speaker_wav("M1") == "male"
    assert normalize_speaker_wav("F1") == "female"
    assert normalize_speaker_wav("female") == "female"


def test_default_tts_provider():
    assert get_tts_provider() in ("multilingual", "supertonic")


def test_resolve_tts_config_language(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "supertonic")
    en = resolve_tts_config("male", language="en")
    ar = resolve_tts_config("female", language="ar")
    assert en["lang"] == "en"
    assert ar["lang"] == "ar"
    assert en["voice"] == "M1"
    assert ar["voice"] == "F1"


def test_turn_latency_tts_calls():
    t = TurnLatency()
    t.record_tts_call(
        TtsCallTiming(text_preview="Hi", chars=2, ttfb_ms=100, total_ms=400, bytes_received=8000)
    )
    t.record_tts_call(
        TtsCallTiming(text_preview="there", chars=5, ttfb_ms=90, total_ms=350, bytes_received=6000)
    )
    payload = t.to_payload()
    assert payload["tts_ttfb_ms"] == 100
    assert payload["tts_total_ms"] == 750
    assert payload["tts_call_count"] == 2
    assert "tts_first_byte_ms" not in payload
    assert len(payload["tts_calls"]) == 2
