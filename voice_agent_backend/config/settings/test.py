from .base import *  # noqa: F403

DEBUG = False

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
VOICE_AGENT_DEV_API_KEY = "test-api-key"
LIVEKIT_URL = "wss://test.livekit.cloud"
LIVEKIT_API_KEY = "testkey"
LIVEKIT_API_SECRET = "testsecret"
DEEPGRAM_API_KEY = "dg-test"
OPENROUTER_API_KEY = "or-test"
TTS_BASE_URL = "http://supertonic-test.example:7788"
CHATTERBOX_TTS_URL = ""
VOICE_AGENT_SKIP_TTS_WARMUP = True
VOICE_AGENT_SKIP_ROOM_SETUP = True
