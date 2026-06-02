import asyncio

import pytest

from agent.pipeline.stt_partial_finalize import PartialFinalizeController


@pytest.mark.asyncio
async def test_partial_finalize_emits_after_timeout():
    finals: list[str] = []

    ctrl = PartialFinalizeController(
        emit_final=lambda t: finals.append(t) or True,
        get_text=lambda: "hello world",
        is_already_final=lambda: False,
        min_chars=3,
        timeout_ms=100,
    )
    ctrl.on_partial("hello")
    await asyncio.sleep(0.15)
    assert finals == ["hello world"]
    await ctrl.aclose()


@pytest.mark.asyncio
async def test_partial_finalize_cancelled_on_new_partial():
    finals: list[str] = []

    ctrl = PartialFinalizeController(
        emit_final=lambda t: finals.append(t) or True,
        get_text=lambda: "hello there",
        is_already_final=lambda: False,
        min_chars=3,
        timeout_ms=200,
    )
    ctrl.on_partial("hello")
    await asyncio.sleep(0.05)
    ctrl.on_partial("hello there")
    await asyncio.sleep(0.25)
    assert finals == ["hello there"]
    await ctrl.aclose()


def test_partial_finalize_timeout_ms_clamped():
    from agent.pipeline.stt_partial_finalize import partial_finalize_timeout_ms

    assert 600 <= partial_finalize_timeout_ms() <= 900
