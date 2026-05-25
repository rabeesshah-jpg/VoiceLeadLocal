"""Persistent aiohttp pool for TTS — keep-alive across synthesis calls."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

import aiohttp

logger = logging.getLogger("agent.pipeline.tts_http_pool")

# Handshake text (minimal synthesis) — Supertonic uses plain text, not Chatterbox "..".
TTS_HANDSHAKE_TEXT = "Hi."


def _warmup_synthesis_text(prefetch_text: str | None) -> str:
    text = (prefetch_text or TTS_HANDSHAKE_TEXT).strip()
    return text if text else TTS_HANDSHAKE_TEXT


_session: aiohttp.ClientSession | None = None
_warmed_keys: set[tuple[str, str, str]] = set()


def _build_connector() -> aiohttp.TCPConnector:
    return aiohttp.TCPConnector(
        limit=32,
        limit_per_host=16,
        enable_cleanup_closed=True,
        keepalive_timeout=120,
    )


def _default_timeout() -> aiohttp.ClientTimeout:
    return aiohttp.ClientTimeout(total=300, connect=30, sock_connect=30)


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
    return sess_loop is current and not sess_loop.is_closed()


async def _discard_stale_session() -> None:
    global _session
    old = _session
    _session = None
    if old is None or old.closed:
        return
    loop = getattr(old, "_loop", None)
    if loop is not None and not loop.is_closed():
        await old.close()
        logger.info("tts_http_session_closed reason=stale_or_replaced")
    else:
        logger.warning("tts_http_session_discarded reason=closed_event_loop")


async def ensure_tts_http_session() -> aiohttp.ClientSession:
    global _session
    if _session is not None and not _session_usable_for_running_loop():
        await _discard_stale_session()
    if _session is not None and not _session.closed:
        return _session
    _session = aiohttp.ClientSession(
        connector=_build_connector(),
        timeout=_default_timeout(),
        headers={"Connection": "keep-alive"},
    )
    logger.info("tts_http_session_created keep_alive=true")
    return _session


# Back-compat alias (Chatterbox integration commented out in entrypoint).
ensure_chatterbox_http_session = ensure_tts_http_session


def _normalize_base(url: str) -> str:
    u = url.strip().rstrip("/")
    for suffix in ("/tts_to_audio", "/v1/tts", "/tts", "/v1/audio/speech"):
        if u.endswith(suffix):
            u = u[: -len(suffix)]
    return u.rstrip("/")


def _tts_provider() -> str:
    return (os.environ.get("TTS_PROVIDER") or "multilingual").strip().lower()


async def prepare_tts_http_for_job(
    base_url: str,
    *,
    voice: str = "female",
    lang: str = "en",
) -> dict[str, Any]:
    return await warmup_tts_connection(base_url, voice=voice, lang=lang)


async def warmup_multilingual_connection(
    base_url: str,
    *,
    voice: str = "female",
    lang: str = "en",
    force: bool = False,
    prefetch_text: str | None = None,
) -> dict[str, Any]:
    """POST /tts_to_audio/ with minimal text to warm HTTP connection."""
    from agent.pipeline.tts_multilingual_server import normalize_speaker_wav

    global _warmed_keys
    base = _normalize_base(base_url)
    lang = (lang or os.environ.get("TTS_LANG") or "en").strip()
    speaker = normalize_speaker_wav(voice or os.environ.get("TTS_VOICE") or "female")

    session = await ensure_tts_http_session()
    warm_key = (base, speaker, lang, "multilingual")
    if not force and warm_key in _warmed_keys:
        return {
            "ok": True,
            "skipped": True,
            "reason": "already_warmed",
            "base_url": base,
            "voice": speaker,
            "lang": lang,
        }

    t0 = time.perf_counter()
    try:
        warmup_text = _warmup_synthesis_text(prefetch_text)
        async with session.post(
            f"{base}/tts_to_audio/",
            json={
                "text": warmup_text,
                "language": lang,
                "speaker_wav": speaker,
            },
        ) as resp:
            if resp.status != 200:
                body = (await resp.text())[:200]
                return {
                    "ok": False,
                    "base_url": base,
                    "status": resp.status,
                    "body": body,
                    "step": "tts_to_audio",
                }
            nbytes = len(await resp.read())

        _warmed_keys.add(warm_key)
        handshake_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "multilingual_connection_warmup ok base=%s speaker=%s lang=%s handshake_ms=%.0f bytes=%s prefetch_chars=%s",
            base,
            speaker,
            lang,
            handshake_ms,
            nbytes,
            len(warmup_text),
        )
        return {
            "ok": nbytes > 0,
            "base_url": base,
            "voice": speaker,
            "lang": lang,
            "handshake_ms": round(handshake_ms, 2),
            "bytes_received": nbytes,
            "sample_rate": 24000,
        }
    except Exception as exc:
        logger.error("multilingual_connection_warmup failed: %s", exc)
        return {"ok": False, "base_url": base, "error": str(exc)}


async def warmup_tts_connection(
    base_url: str,
    *,
    voice: str = "female",
    lang: str = "en",
    force: bool = False,
    prefetch_text: str | None = None,
) -> dict[str, Any]:
    if _tts_provider() == "supertonic":
        return await warmup_supertonic_connection(
            base_url,
            voice=voice,
            lang=lang,
            force=force,
            prefetch_text=prefetch_text,
        )
    return await warmup_multilingual_connection(
        base_url,
        voice=voice,
        lang=lang,
        force=force,
        prefetch_text=prefetch_text,
    )


prepare_chatterbox_http_for_job = prepare_tts_http_for_job


async def warmup_supertonic_connection(
    base_url: str,
    *,
    voice: str = "M1",
    lang: str = "en",
    force: bool = False,
    prefetch_text: str | None = None,
) -> dict[str, Any]:
    """
    GET /v1/health + minimal POST /v1/tts to open TCP/TLS for this job.

    Later POST /v1/tts calls reuse the same aiohttp session (HTTP keep-alive).
    """
    global _warmed_keys
    base = _normalize_base(base_url)
    lang = (lang or os.environ.get("TTS_LANG") or "en").strip()
    voice = (voice or os.environ.get("TTS_VOICE") or "M1").strip()

    session = await ensure_tts_http_session()
    warm_key = (base, voice, lang)
    if not force and warm_key in _warmed_keys:
        return {
            "ok": True,
            "skipped": True,
            "reason": "already_warmed",
            "base_url": base,
            "voice": voice,
            "lang": lang,
        }

    t0 = time.perf_counter()
    try:
        async with session.get(f"{base}/v1/health") as resp:
            health = await resp.json() if resp.status == 200 else {}
            if resp.status != 200:
                return {
                    "ok": False,
                    "base_url": base,
                    "status": resp.status,
                    "step": "health",
                }

        warmup_text = _warmup_synthesis_text(prefetch_text)
        async with session.post(
            f"{base}/v1/tts",
            json={
                "text": warmup_text,
                "voice": voice,
                "lang": lang,
                "response_format": "wav",
            },
        ) as resp:
            if resp.status != 200:
                body = (await resp.text())[:200]
                return {
                    "ok": False,
                    "base_url": base,
                    "status": resp.status,
                    "body": body,
                    "step": "tts",
                }
            nbytes = len(await resp.read())

        _warmed_keys.add(warm_key)
        handshake_ms = (time.perf_counter() - t0) * 1000
        logger.info(
            "supertonic_connection_warmup ok base=%s voice=%s lang=%s handshake_ms=%.0f bytes=%s prefetch_chars=%s model=%s",
            base,
            voice,
            lang,
            handshake_ms,
            nbytes,
            len(warmup_text),
            health.get("model"),
        )
        return {
            "ok": nbytes > 0,
            "base_url": base,
            "voice": voice,
            "lang": lang,
            "handshake_ms": round(handshake_ms, 2),
            "bytes_received": nbytes,
            "model": health.get("model"),
            "sample_rate": health.get("sample_rate"),
        }
    except Exception as exc:
        logger.error("supertonic_connection_warmup failed: %s", exc)
        return {"ok": False, "base_url": base, "error": str(exc)}


warmup_chatterbox_connection = warmup_supertonic_connection


async def close_tts_http_session() -> None:
    global _warmed_keys
    await _discard_stale_session()
    _warmed_keys = set()


close_chatterbox_http_session = close_tts_http_session


def warmup_tts_connection_sync(
    base_url: str,
    *,
    voice: str = "female",
    lang: str = "en",
    force: bool = False,
) -> dict[str, Any]:
    async def _run() -> dict[str, Any]:
        try:
            return await warmup_tts_connection(
                base_url, voice=voice, lang=lang, force=force
            )
        finally:
            await close_tts_http_session()

    return asyncio.run(_run())


warmup_supertonic_connection_sync = warmup_tts_connection_sync
warmup_chatterbox_connection_sync = warmup_tts_connection_sync
