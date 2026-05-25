import pytest

from agent.pipeline import user_transcript_display as display


def test_needs_translation_for_arabic():
    assert display.needs_english_ui_translation("مرحباً كيف حالك")


def test_no_translation_for_english():
    assert not display.needs_english_ui_translation("And how are you?")


@pytest.mark.asyncio
async def test_user_message_for_ui_streams_non_english_partial():
    assert await display.user_message_for_ui("مرحباً", is_final=False) == "مرحباً"


@pytest.mark.asyncio
async def test_user_message_for_ui_passes_english_partial():
    assert await display.user_message_for_ui("Hello there", is_final=False) == "Hello there"


@pytest.mark.asyncio
async def test_translate_to_english_uses_api(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"choices": [{"message": {"content": "Hello"}}]}

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def post(self, *args, **kwargs):
            return FakeResponse()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(display.httpx, "AsyncClient", lambda **kw: FakeClient())
    assert await display.translate_to_english("مرحباً") == "Hello"
