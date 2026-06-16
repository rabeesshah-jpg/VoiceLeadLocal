"""Worker-side environment helpers (RunPod vs local)."""

from __future__ import annotations

import os

DEFAULT_RUNPOD_TTS_BASE = "http://127.0.0.1:7788"
DEFAULT_TTS_HEALTH_PATH = "/v1/health"
_RUNPOD_REMOTE_STT = "deepgram"


def worker_env() -> str:
    return (os.environ.get("WORKER_ENV") or "local").strip().lower()


def is_runpod_worker() -> bool:
    return worker_env() == "runpod"


def cuda_visible_devices() -> str:
    """Value of CUDA_VISIBLE_DEVICES in this worker process (empty string = no GPU)."""
    return os.environ.get("CUDA_VISIBLE_DEVICES", "")


def worker_cpu_only() -> bool:
    raw = (os.environ.get("WORKER_CPU_ONLY") or "").strip().lower()
    if raw in ("1", "true", "yes", "on"):
        return True
    if raw in ("0", "false", "no", "off"):
        return False
    return is_runpod_worker()


def enforce_worker_runtime_policy() -> None:
    """Apply RunPod worker constraints to this process only (not global pod env).

    - Hides CUDA from the worker so TTS (separate process) keeps GPU access.
    - Forces remote Deepgram STT on RunPod (no local Whisper/GPU STT).
    """
    if is_runpod_worker():
        prior = (os.environ.get("STT_PROVIDER") or "").strip().lower()
        if prior and prior != _RUNPOD_REMOTE_STT:
            import logging

            logging.getLogger("agent.worker_env").warning(
                "RunPod worker overrides STT_PROVIDER=%r -> %r (CPU-only remote STT)",
                prior,
                _RUNPOD_REMOTE_STT,
            )
        os.environ["STT_PROVIDER"] = _RUNPOD_REMOTE_STT

    if not worker_cpu_only():
        return

    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ.setdefault("WORKER_CPU_ONLY", "true")


def deepgram_remote_client() -> bool:
    provider = (os.environ.get("STT_PROVIDER") or _RUNPOD_REMOTE_STT).strip().lower()
    return provider == _RUNPOD_REMOTE_STT and bool(
        (os.environ.get("DEEPGRAM_API_KEY") or "").strip()
    )


def openrouter_remote_client() -> bool:
    return bool((os.environ.get("OPENROUTER_API_KEY") or "").strip())


def tts_health_check_label(ok: bool | None) -> str:
    if ok is True:
        return "pass"
    if ok is False:
        return "fail"
    return "unknown"


def database_url_configured() -> bool:
    return bool((os.environ.get("DATABASE_URL") or "").strip())


def resolve_openrouter_model(default: str = "openai/gpt-4o-mini") -> str:
    return (
        (os.environ.get("OPENROUTER_MODEL") or "").strip()
        or (os.environ.get("VOICE_AGENT_LLM_MODEL") or "").strip()
        or default
    )


def resolve_tts_base_url() -> str:
    explicit = (
        (os.environ.get("TTS_BASE_URL") or "").strip()
        or (os.environ.get("CHATTERBOX_TTS_URL") or "").strip()
    )
    if explicit:
        return explicit.rstrip("/")
    if is_runpod_worker():
        return DEFAULT_RUNPOD_TTS_BASE
    return DEFAULT_RUNPOD_TTS_BASE


def resolve_tts_health_url() -> str:
    explicit = (os.environ.get("TTS_HEALTH_URL") or "").strip()
    if explicit:
        return explicit.rstrip("/")
    base = resolve_tts_base_url()
    return f"{base}{DEFAULT_TTS_HEALTH_PATH}"
