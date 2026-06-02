"""TurnCommitController and STT delta streaming guards."""

import asyncio

import pytest

from agent.pipeline.stt_transcript_utils import extract_stt_delta
from agent.pipeline.user_turn_streaming import TurnCommitController, final_stability_ms


def test_extract_stt_delta_strips_committed_prefix():
    prior = "here how are you? I want to build a website..."
    incoming = (
        "here how are you? I want to build a website... My name is Ali. Bye"
    )
    assert extract_stt_delta(prior, incoming) == "My name is Ali. Bye"


def test_extract_stt_delta_empty_when_unchanged():
    text = "hello world"
    assert extract_stt_delta(text, text) == ""


@pytest.mark.asyncio
async def test_schedule_final_commit_debounced():
    controller = TurnCommitController()
    commits: list[str] = []

    async def commit_fn(**kw):
        commits.append(kw["full_text"])

    controller.schedule_final_commit(
        turn_id="t1",
        room="r1",
        turn_seq=1,
        full_text="first",
        commit_fn=commit_fn,
    )
    await asyncio.sleep(final_stability_ms() / 1000.0 + 0.05)
    assert commits == ["first"]


@pytest.mark.asyncio
async def test_superseding_final_cancels_pending_commit(monkeypatch):
    monkeypatch.setenv("VOICE_AGENT_STT_FINAL_STABILITY_MS", "200")
    monkeypatch.setenv("STT_PROVIDER", "faster_whisper")
    controller = TurnCommitController()
    commits: list[str] = []

    async def commit_fn(**kw):
        commits.append(kw["full_text"])

    controller.schedule_final_commit(
        turn_id="t1",
        room="r1",
        turn_seq=1,
        full_text="short",
        commit_fn=commit_fn,
    )
    await asyncio.sleep(0.05)
    controller.schedule_final_commit(
        turn_id="t1",
        room="r1",
        turn_seq=1,
        full_text="short extended text",
        commit_fn=commit_fn,
    )
    await asyncio.sleep(final_stability_ms() / 1000.0 + 0.05)
    assert commits == ["short extended text"]


def test_should_send_final_rejects_already_sent():
    controller = TurnCommitController()
    controller.record_sent_final("My name is Ali")
    ok, reason = controller.should_send_final("My name is Ali")
    assert not ok
    assert reason == "already_sent"


def test_final_stability_ms_zero_for_deepgram(settings):
    settings.STT_PROVIDER = "deepgram"
    assert final_stability_ms() == 0


def test_final_stability_ms_default_for_faster_whisper(settings, monkeypatch):
    settings.STT_PROVIDER = "faster_whisper"
    monkeypatch.delenv("VOICE_AGENT_STT_FINAL_STABILITY_MS", raising=False)
    assert final_stability_ms() == 200


def test_allow_llm_request_blocks_duplicate_pipeline_turn():
    controller = TurnCommitController()
    assert controller.allow_llm_request(
        turn_seq=1, pipeline_turn_id="abc", room="r1"
    )
    controller.mark_llm_started(turn_seq=1, pipeline_turn_id="abc")
    assert not controller.allow_llm_request(
        turn_seq=1, pipeline_turn_id="abc", room="r1"
    )
