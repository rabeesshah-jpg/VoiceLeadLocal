"""Turn generation tracking for barge-in stale completion guard."""

from __future__ import annotations

import asyncio


class GenerationController:
    """Increment generation on interrupt; callers check is_current before applying results."""

    def __init__(self):
        self._generation = 0
        self._lock = asyncio.Lock()

    @property
    def generation(self) -> int:
        return self._generation

    async def bump(self) -> int:
        async with self._lock:
            self._generation += 1
            return self._generation

    def is_current(self, gen: int) -> bool:
        return gen == self._generation
