"""Accumulate Deepgram final segments into one utterance for UI / data channel."""

from __future__ import annotations


def merge_stt_segment(parts: list[str], segment: str) -> list[str]:
    """Append a final or partial STT segment without dropping earlier words."""
    segment = segment.strip()
    if not segment:
        return parts

    if not parts:
        return [segment]

    joined = " ".join(parts)
    if segment == joined or segment.startswith(joined):
        return [segment]
    if joined.endswith(segment) or segment in joined:
        return parts

    return [*parts, segment]


def joined_transcript(parts: list[str], *, interim: str = "") -> str:
    """Full transcript for display: committed segments plus optional interim tail."""
    base = " ".join(p.strip() for p in parts if p.strip())
    interim = interim.strip()
    if not interim:
        return base
    if not base:
        return interim
    if interim.startswith(base):
        return interim
    if base.endswith(interim):
        return base
    return f"{base} {interim}"
