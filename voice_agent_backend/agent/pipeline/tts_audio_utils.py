"""PCM/WAV helpers for gapless multi-sentence TTS playback."""

from __future__ import annotations

import os
import struct

WAV_HEADER_BYTES = 44


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def strip_leading_wav_header(data: bytes) -> bytes:
    if len(data) >= WAV_HEADER_BYTES and data[:4] == b"RIFF":
        return data[WAV_HEADER_BYTES:]
    return data


def trim_leading_pcm_silence(
    pcm: bytes,
    *,
    threshold: int | None = None,
    max_trim_ms: int | None = None,
    sample_rate: int = 24000,
) -> bytes:
    """Remove leading near-zero samples (common when concatenating WAV segments)."""
    if len(pcm) < 4:
        return pcm
    thresh = threshold if threshold is not None else _env_int(
        "VOICE_AGENT_TTS_SILENCE_THRESHOLD", 400
    )
    max_ms = max_trim_ms if max_trim_ms is not None else _env_int(
        "VOICE_AGENT_TTS_MAX_LEADING_TRIM_MS", 80
    )
    max_samples = int(sample_rate * max_ms / 1000)
    count = len(pcm) // 2
    samples = struct.unpack(f"<{count}h", pcm[: count * 2])
    start = 0
    for i, sample in enumerate(samples):
        if abs(sample) > thresh:
            start = i
            break
        if i >= max_samples:
            start = i + 1
            break
    else:
        return pcm
    if start <= 0:
        return pcm
    trimmed = samples[start:]
    return struct.pack(f"<{len(trimmed)}h", *trimmed)


def trim_trailing_pcm_silence(
    pcm: bytes,
    *,
    threshold: int | None = None,
    max_trim_ms: int | None = None,
    sample_rate: int = 24000,
) -> bytes:
    """Remove trailing near-zero samples (reduces pause between sentence clips)."""
    if len(pcm) < 4:
        return pcm
    thresh = threshold if threshold is not None else _env_int(
        "VOICE_AGENT_TTS_SILENCE_THRESHOLD", 400
    )
    max_ms = max_trim_ms if max_trim_ms is not None else _env_int(
        "VOICE_AGENT_TTS_MAX_TRAILING_TRIM_MS", 180
    )
    max_samples = int(sample_rate * max_ms / 1000)
    count = len(pcm) // 2
    samples = struct.unpack(f"<{count}h", pcm[: count * 2])
    end = count
    trimmed_from_end = 0
    for i in range(count - 1, -1, -1):
        if abs(samples[i]) > thresh:
            end = i + 1
            break
        trimmed_from_end += 1
        if trimmed_from_end >= max_samples:
            end = i
            break
    if end >= count:
        return pcm
    trimmed = samples[:end]
    return struct.pack(f"<{len(trimmed)}h", *trimmed)


def prepare_phrase_pcm(
    data: bytes,
    *,
    sample_rate: int,
    strip_wav_header: bool,
    trim_leading: bool,
    trim_trailing: bool,
) -> bytes:
    pcm = strip_leading_wav_header(data) if strip_wav_header else data
    if trim_leading and strip_wav_header:
        pcm = trim_leading_pcm_silence(pcm, sample_rate=sample_rate)
    if trim_trailing:
        pcm = trim_trailing_pcm_silence(pcm, sample_rate=sample_rate)
    return pcm


def pcm_to_emit_chunks(pcm: bytes, chunk_bytes: int) -> list[bytes]:
    if not pcm:
        return []
    return [pcm[i : i + chunk_bytes] for i in range(0, len(pcm), chunk_bytes)]
