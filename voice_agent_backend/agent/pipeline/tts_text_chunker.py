"""Split streaming LLM text at sentence boundaries for TTS requests.

Avoids mid-clause splits on commas, colons, or arbitrary character limits — those
cause audible pauses between back-to-back HTTP synthesis calls.
"""

from __future__ import annotations

import re

# Sentence terminators only (not comma, colon, or semicolon).
_SENTENCE_END_RE = re.compile(r'[.!?]["\']?\s*$')
# Prior complete sentence inside a long run-on buffer (overflow fallback).
_SENTENCE_BOUNDARY_RE = re.compile(r'[.!?]["\']?\s+')


def ends_sentence(text: str) -> bool:
    """True when *text* ends with terminal sentence punctuation."""
    return bool(_SENTENCE_END_RE.search(text.strip()))


def _last_sentence_boundary_end(buf: str, *, min_prefix: int) -> int | None:
    """Index after the last in-buffer sentence boundary, or None."""
    last: int | None = None
    for match in _SENTENCE_BOUNDARY_RE.finditer(buf):
        if match.end() >= min_prefix:
            last = match.end()
    return last


class SentenceTextChunker:
    """Buffer LLM tokens; emit only on sentence end or final flush."""

    def __init__(self, *, min_chars: int = 8, max_chars: int = 2000) -> None:
        self._min_chars = max(1, min_chars)
        # max_chars: only used when a single sentence exceeds this length (rare).
        self._max_chars = max(self._min_chars + 1, max_chars)
        self._parts: list[str] = []

    def _buffer(self) -> str:
        return "".join(self._parts)

    def push(self, text: str) -> list[str]:
        if text:
            self._parts.append(text)
        return self._emit(force=False)

    def flush(self) -> list[str]:
        return self._emit(force=True)

    def _emit(self, *, force: bool) -> list[str]:
        out: list[str] = []
        while True:
            buf = self._buffer().strip()
            if not buf:
                self._parts.clear()
                break
            if force:
                out.append(buf)
                self._parts.clear()
                break
            if len(buf) >= self._min_chars and ends_sentence(buf):
                out.append(buf)
                self._parts.clear()
                break
            if len(buf) >= self._max_chars:
                cut_end = _last_sentence_boundary_end(buf, min_prefix=self._min_chars)
                if cut_end is not None:
                    out.append(buf[:cut_end].strip())
                    remainder = buf[cut_end:].lstrip()
                    self._parts = [remainder] if remainder else []
                    continue
                # No boundary yet — keep buffering (do not split mid-sentence).
            break
        return out
