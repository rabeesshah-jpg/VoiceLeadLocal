from agent.pipeline.tts_audio_utils import (
    is_wav,
    parse_wav_info,
    prepare_phrase_pcm,
    trim_trailing_pcm_silence,
)


def test_trim_trailing_silence():
    samples = [0, 0, 100, 200, 0, 0]
    import struct

    pcm = struct.pack(f"<{len(samples)}h", *samples)
    trimmed = trim_trailing_pcm_silence(pcm, sample_rate=24000, threshold=50)
    out = struct.unpack(f"<{len(trimmed)//2}h", trimmed)
    assert out[-1] == 200


def test_prepare_phrase_strips_and_trims():
    import struct

    header = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 32
    pcm = struct.pack("<4h", 0, 0, 500, 500)
    wav = header + pcm
    result = prepare_phrase_pcm(
        wav,
        sample_rate=44100,
        strip_wav_header=True,
        trim_leading=False,
        trim_trailing=True,
    )
    assert not result.startswith(b"RIFF")
    assert len(result) > 0


def test_full_wav_not_corrupted_by_trim():
    """Regression: trimming must not run on intact WAV (only on stripped PCM)."""
    import struct

    pcm = struct.pack("<100h", *([1000] * 100))
    data_size = len(pcm)
    riff_size = 36 + data_size
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        riff_size,
        b"WAVE",
        b"fmt ",
        16,
        1,
        1,
        44100,
        44100 * 2,
        2,
        16,
        b"data",
        data_size,
    )
    wav = header + pcm
    assert is_wav(wav)
    info = parse_wav_info(wav)
    assert info["valid"]
    assert info["pcm_frames"] == 100
