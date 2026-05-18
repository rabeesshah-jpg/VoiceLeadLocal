"""Simple PCM resampling utilities (optional post-processing)."""

from __future__ import annotations

import numpy as np


def resample_pcm16_mono(pcm: bytes, from_rate: int, to_rate: int) -> bytes:
    if from_rate == to_rate or not pcm:
        return pcm
    samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float32)
    if len(samples) == 0:
        return pcm
    duration = len(samples) / from_rate
    out_len = int(duration * to_rate)
    if out_len <= 0:
        return b""
    x_old = np.linspace(0, 1, num=len(samples), endpoint=False)
    x_new = np.linspace(0, 1, num=out_len, endpoint=False)
    out = np.interp(x_new, x_old, samples).astype(np.int16)
    return out.tobytes()
