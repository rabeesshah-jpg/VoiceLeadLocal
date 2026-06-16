"""
Voice agent logging: formatted file output + clear on each process start.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

LOG_DIR_NAME = "logs"
API_LOG_NAME = "voice_agent_api.log"
WORKER_LOG_NAME = "voice_agent_worker.log"
PIPELINE_LATENCY_LOG_NAME = "voice_agent_pipeline_latency.log"
UI_TELEMETRY_LOG_NAME = "voice_agent_ui_telemetry.log"
COMBINED_LOG_NAME = "voice_agent.log"

_api_configured = False
_ui_telemetry_configured = False
_worker_configured = False
_pipeline_latency_configured = False


class VoiceAgentFormatter(logging.Formatter):
    """Readable multi-line friendly formatter."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
        level = record.levelname.ljust(8)
        name = record.name
        msg = record.getMessage()
        if "\n" in msg:
            return f"{ts} | {level} | {name}\n{msg}"
        return f"{ts} | {level} | {name} | {msg}"


def _log_dir(base_dir: Path) -> Path:
    d = base_dir / LOG_DIR_NAME
    d.mkdir(exist_ok=True)
    return d


def _write_session_header(path: Path, process_label: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("\n")
        f.write("=" * 80 + "\n")
        f.write(f"  VOICE AGENT — {process_label}\n")
        f.write(f"  session started: {datetime.now().isoformat(timespec='seconds')}\n")
        f.write(f"  pid: {os.getpid()}\n")
        f.write("=" * 80 + "\n\n")


def _attach_file_handler(
    *,
    log_path: Path,
    process_label: str,
    logger_names: tuple[str, ...],
    level: int = logging.DEBUG,
) -> None:
    _write_session_header(log_path, process_label)
    formatter = VoiceAgentFormatter()
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(logging.INFO)

    for name in logger_names:
        log = logging.getLogger(name)
        log.setLevel(level)
        log.handlers.clear()
        log.propagate = False
        log.addHandler(file_handler)
        log.addHandler(console_handler)

    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(console_handler)
    root.setLevel(logging.INFO)

    startup = logging.getLogger(logger_names[0])
    startup.info(
        "\n%s\n  logging initialized\n  log_file ....... %s\n%s",
        "=" * 80,
        log_path,
        "=" * 80,
    )


def setup_api_logging(base_dir: Path) -> Path:
    """Clear API log file and configure handlers. Call once per API process."""
    global _api_configured
    if _api_configured:
        return _log_dir(base_dir) / API_LOG_NAME
    _api_configured = True

    log_path = _log_dir(base_dir) / API_LOG_NAME
    _attach_file_handler(
        log_path=log_path,
        process_label="API (Django)",
        logger_names=(
            "apps.calls",
            "apps.calls.views",
            "apps.calls.auth",
            "apps.calls.http",
            "apps.calls.services",
            "django.request",
        ),
    )
    return log_path


def setup_ui_telemetry_logging(base_dir: Path) -> Path:
    """Browser UI telemetry (audio received, playback, transcript rendered)."""
    global _ui_telemetry_configured
    if _ui_telemetry_configured:
        return _log_dir(base_dir) / UI_TELEMETRY_LOG_NAME
    _ui_telemetry_configured = True

    log_path = _log_dir(base_dir) / UI_TELEMETRY_LOG_NAME
    _attach_file_handler(
        log_path=log_path,
        process_label="UI TELEMETRY (browser → API)",
        logger_names=("apps.calls.ui_telemetry",),
    )
    return log_path


def setup_pipeline_latency_logging(base_dir: Path) -> Path:
    """Dedicated file for STT → LLM → TTS milestone timings (worker only)."""
    global _pipeline_latency_configured
    log_path = _log_dir(base_dir) / PIPELINE_LATENCY_LOG_NAME
    if _pipeline_latency_configured:
        return log_path
    _pipeline_latency_configured = True

    _write_session_header(log_path, "PIPELINE LATENCY (STT / LLM / TTS)")
    formatter = VoiceAgentFormatter()
    file_handler = logging.FileHandler(log_path, mode="a", encoding="utf-8")
    file_handler.setFormatter(formatter)
    file_handler.setLevel(logging.INFO)

    for log_name in (
        "agent.observability.pipeline_latency",
        "agent.observability.pipeline_events",
    ):
        plog = logging.getLogger(log_name)
        plog.setLevel(logging.INFO)
        plog.handlers.clear()
        plog.propagate = False
        plog.addHandler(file_handler)

    plog = logging.getLogger("agent.observability.pipeline_latency")
    plog.info(
        "\n%s\n  pipeline latency log initialized\n  log_file ....... %s\n%s",
        "=" * 80,
        log_path,
        "=" * 80,
    )
    return log_path


def setup_worker_logging(base_dir: Path) -> Path:
    """Clear worker log file and configure handlers. Call once per worker process."""
    global _worker_configured
    if _worker_configured:
        return _log_dir(base_dir) / WORKER_LOG_NAME
    _worker_configured = True

    log_path = _log_dir(base_dir) / WORKER_LOG_NAME
    _attach_file_handler(
        log_path=log_path,
        process_label="WORKER (LiveKit Agent)",
        logger_names=(
            "agent",
            "agent.entrypoint",
            "agent.pipeline",
            "agent.observability",
            "agent.voice_agent",
        ),
    )
    setup_pipeline_latency_logging(base_dir)
    return log_path


def should_reset_api_logs_on_ready() -> bool:
    """Avoid clearing logs twice under runserver autoreloader parent."""
    if os.environ.get("RUN_MAIN") == "true":
        return True
    if "runserver" not in sys.argv:
        return True
    return False
