import pytest

from agent.pipeline.interrupt import GenerationController
from agent.observability.latency import TurnLatency


@pytest.mark.asyncio
async def test_generation_controller_bump():
    ctrl = GenerationController()
    gen1 = await ctrl.bump()
    assert ctrl.is_current(gen1)
    gen2 = await ctrl.bump()
    assert not ctrl.is_current(gen1)
    assert ctrl.is_current(gen2)


def test_turn_latency_payload():
    t = TurnLatency()
    t.mark_stt_final()
    t.mark_llm_first_token()
    payload = t.to_payload()
    assert "turn_id" in payload
    assert payload["stt_ms"] is None or isinstance(payload["stt_ms"], int)
