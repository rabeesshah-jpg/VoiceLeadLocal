"""Shared aiohttp session for Deepgram STT — reuse TLS/DNS across streams."""

from __future__ import annotations

import asyncio
import logging

import aiohttp

logger = logging.getLogger("agent.pipeline.stt_http_pool")

_session: aiohttp.ClientSession | None = None


def _build_connector() -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(
        limit=32,
        limit_per_host=16,
        enable_cleanup_closed=True,
        keepalive_timeout=120,
    )


def _default_timeout() -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(total=300, connect=15, sock_connect=15)


def _session_usable_for_running_loop() -> bool:
    if _session is None or _session.closed:
        return False
    try:
        current = asyncio.get_running_loop()
    except RuntimeError:
        return False
    sess_loop = getattr(_session, "_loop", None)
    if sess_loop is None:
        return True
    return sess_loop is current


async def ensure_stt_http_session() -> aiohttp.ClientSession:
    global _session
    if _session_usable_for_running_loop():
        return _session  # type: ignore[return-value]
    if _session is not None and not _session.closed:
        await _session.close()
    _session = aiohttp.ClientSession(
        connector=_build_connector(),
        timeout=_default_timeout(),
    )
    logger.debug("stt_http_session created")
    return _session
