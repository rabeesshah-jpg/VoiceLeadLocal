"""
Structured step logging with per-component timers.

Use StepTimer as a context manager around each pipeline step.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator


def _fmt_kv(key: str, value: Any, width: int = 16) -> str:
    label = f"  {key}".ljust(width)
    if value is None:
        return f"{label} —"
    text = str(value)
    if "\n" in text:
        lines = text.split("\n")
        out = [f"{label} {lines[0]}"]
        pad = " " * width
        for line in lines[1:]:
            out.append(f"{pad} {line}")
        return "\n".join(out)
    return f"{label} {text}"


def _separator(char: str = "─", width: int = 80) -> str:
    return char * width


def log_block(
    logger: logging.Logger,
    level: int,
    *,
    operation: str,
    step: str,
    status: str,
    duration_ms: int | float | None = None,
    include_none: bool = False,
    **fields: Any,
) -> None:
    lines = [
        _separator(),
        _fmt_kv("operation", operation),
        _fmt_kv("step", step),
        _fmt_kv("status", status),
    ]
    if duration_ms is not None:
        lines.append(_fmt_kv("duration_ms", f"{duration_ms:.2f}" if isinstance(duration_ms, float) else duration_ms))
    for key, value in fields.items():
        if not include_none and (value is None or value == ""):
            continue
        display = "null" if value is None else value
        if display == "" and not include_none:
            continue
        lines.append(_fmt_kv(key.replace("_", " "), display))
    lines.append(_separator())
    logger.log(level, "\n%s", "\n".join(lines))


class StepTimer:
    """Time a single step and emit BEGIN / OK / FAIL blocks."""

    def __init__(
        self,
        logger: logging.Logger,
        operation: str,
        step: str,
        **fields: Any,
    ):
        self.logger = logger
        self.operation = operation
        self.step = step
        self.fields = fields
        self._t0 = 0.0

    def __enter__(self) -> StepTimer:
        self._t0 = time.perf_counter()
        log_block(
            self.logger,
            logging.INFO,
            operation=self.operation,
            step=self.step,
            status="BEGIN",
            **self.fields,
        )
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
        elapsed_ms = (time.perf_counter() - self._t0) * 1000
        if exc_type is not None:
            log_block(
                self.logger,
                logging.ERROR,
                operation=self.operation,
                step=self.step,
                status="FAIL",
                duration_ms=elapsed_ms,
                error_type=exc_type.__name__,
                error=str(exc_val)[:500],
                **self.fields,
            )
            return False
        log_block(
            self.logger,
            logging.INFO,
            operation=self.operation,
            step=self.step,
            status="OK",
            duration_ms=elapsed_ms,
            **self.fields,
        )
        return False

    def add_fields(self, **kwargs: Any) -> None:
        self.fields.update(kwargs)


@contextmanager
def timed_step(
    logger: logging.Logger,
    operation: str,
    step: str,
    **fields: Any,
) -> Iterator[StepTimer]:
    with StepTimer(logger, operation, step, **fields) as timer:
        yield timer
