import time

from agent.pipeline.partial_stt_preemptive import PartialSttPreemptivePolicy


def test_policy_requires_min_chars():
    p = PartialSttPreemptivePolicy(enabled=True, min_chars=10)
    assert not p.should_emit_preflight("hi there")
    assert p.should_emit_preflight("hello world")


def test_policy_rate_limits_small_growth():
    p = PartialSttPreemptivePolicy(
        enabled=True,
        min_chars=8,
        min_growth_chars=6,
        min_interval_s=1.0,
    )
    t0 = 1000.0
    assert p.should_emit_preflight("hello world", now=t0)
    p.mark_emitted("hello world", now=t0)
    assert not p.should_emit_preflight("hello worl", now=t0 + 0.1)
    assert not p.should_emit_preflight("hello world!", now=t0 + 0.2)
    assert p.should_emit_preflight("hello world again", now=t0 + 1.1)


def test_policy_reset_allows_immediate_preflight():
    p = PartialSttPreemptivePolicy(enabled=True, min_chars=8, min_interval_s=1.0)
    t0 = 1000.0
    p.mark_emitted("first phrase", now=t0)
    p.reset()
    assert p.should_emit_preflight("second phrase", now=t0 + 0.05)


def test_from_env(monkeypatch):
    monkeypatch.setenv("VOICE_AGENT_PARTIAL_STT_PREEMPTIVE", "true")
    monkeypatch.setenv("VOICE_AGENT_PARTIAL_STT_MIN_CHARS", "15")
    p = PartialSttPreemptivePolicy.from_env()
    assert p.enabled
    assert p.min_chars == 15
