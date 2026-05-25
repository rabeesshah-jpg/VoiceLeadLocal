import struct

from agent.pipeline.tts_audio_utils import (
    prepare_phrase_pcm,
    trim_trailing_pcm_silence,
)


def _pcm_silence_ms(ms: int, sample_rate: int = 24000) -> bytes:
    n = int(sample_rate * ms / 1000)
    return struct.pack(f"<{n}h", *([0] * n))


def _pcm_tone_ms(ms: int, sample_rate: int = 24000, amp: int = 8000) -> bytes:
    n = int(sample_rate * ms / 1000)
    return struct.pack(f"<{n}h", *([amp] * n))


def test_trim_trailing_silence():
    pcm = _pcm_tone_ms(50) + _pcm_silence_ms(120)
    trimmed = trim_trailing_pcm_silence(pcm, sample_rate=24000, max_trim_ms=200)
    assert len(trimmed) < len(pcm)
    assert len(trimmed) == len(_pcm_tone_ms(50))


def test_prepare_phrase_strips_and_trims():
    silence = _pcm_silence_ms(30)
    tone = _pcm_tone_ms(40)
    wav = b"RIFF" + b"\x00" * 40 + silence + tone + _pcm_silence_ms(100)
    out = prepare_phrase_pcm(
        wav,
        sample_rate=24000,
        strip_wav_header=True,
        trim_leading=True,
        trim_trailing=True,
    )
    assert not out.startswith(b"RIFF")
    assert len(out) < len(silence + tone + _pcm_silence_ms(100))
