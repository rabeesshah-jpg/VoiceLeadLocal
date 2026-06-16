"""Voice agent API settings."""

from pathlib import Path

import environ
from corsheaders.defaults import default_headers

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env(
    DEBUG=(bool, False),
    VOICE_AGENT_TOKEN_TTL_SECONDS=(int, 1800),
)

env_file = BASE_DIR / ".env"
if env_file.exists():
    environ.Env.read_env(str(env_file))
parent_env = BASE_DIR.parent / ".env"
if parent_env.exists():
    environ.Env.read_env(str(parent_env), overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY", default="voice-agent-dev-secret-change-me")
DEBUG = env.bool("DEBUG", default=True)
ALLOWED_HOSTS = env.list("ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",
    "rest_framework",
    "apps.calls.apps.CallsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "config.middleware.RequestLoggingMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
    )
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:5173", "http://127.0.0.1:5173"],
)
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_HEADERS = list(default_headers) + [
    "x-voice-agent-dev-key",
    "x-request-id",
]

REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    "UNAUTHENTICATED_USER": None,
}

# LiveKit
LIVEKIT_URL = env("LIVEKIT_URL", default="")
LIVEKIT_API_KEY = env("LIVEKIT_API_KEY", default="")
LIVEKIT_API_SECRET = env("LIVEKIT_API_SECRET", default="")
VOICE_AGENT_TOKEN_TTL_SECONDS = env.int("VOICE_AGENT_TOKEN_TTL_SECONDS", default=1800)
VOICE_AGENT_DEV_API_KEY = env("VOICE_AGENT_DEV_API_KEY", default="dev-local-key")

# Providers (validated at call start; used by agent worker via env)
STT_PROVIDER = env(
    "STT_PROVIDER",
    default=env("VOICE_AGENT_STT_PROVIDER", default="faster_whisper"),
)
STT_BASE_URL = env("STT_BASE_URL", default="")
STT_WS_URL = env("STT_WS_URL", default="")
STT_LANGUAGE = env("STT_LANGUAGE", default="")
FASTER_WHISPER_STT_URL = env("FASTER_WHISPER_STT_URL", default="http://localhost:8000")
FASTER_WHISPER_LANGUAGE = env("FASTER_WHISPER_LANGUAGE", default="auto")
FASTER_WHISPER_STREAMING = env.bool("FASTER_WHISPER_STREAMING", default=True)
FASTER_WHISPER_TIMEOUT_SECONDS = env.int("FASTER_WHISPER_TIMEOUT_SECONDS", default=30)
DEEPGRAM_API_KEY = env("DEEPGRAM_API_KEY", default="")
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", default="")
OPENROUTER_BASE_URL = env("OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1")
VOICE_AGENT_LLM_MODEL = env("VOICE_AGENT_LLM_MODEL", default="openai/gpt-4o-mini")
# TTS: supertonic (POST /v1/tts) or multilingual (POST /tts_to_audio/)
TTS_PROVIDER = env("TTS_PROVIDER", default="supertonic")
# Optional on Django API (worker calls TTS). Set only for local all-in-one dev or voice-profile admin.
TTS_BASE_URL = env("TTS_BASE_URL", default="")
# Local Django → RunPod TTS public URL for voice-profile clone/upload/capabilities only.
# Worker on RunPod continues to use TTS_BASE_URL=http://127.0.0.1:7788 separately.
VOICE_PROFILE_TTS_BASE_URL = env("VOICE_PROFILE_TTS_BASE_URL", default="")
TTS_MODEL = env("TTS_MODEL", default="multilingual")
TTS_VOICE = env("TTS_VOICE", default="M1")
TTS_LANG = env("TTS_LANG", default="en")
TTS_MAX_CHUNK_LENGTH = env.int("TTS_MAX_CHUNK_LENGTH", default=300)
TTS_VOICE_CLONING_ENABLED = env.bool("TTS_VOICE_CLONING_ENABLED", default=True)
TTS_CLONE_ENDPOINT = env("TTS_CLONE_ENDPOINT", default="/v1/voices/clone")
TTS_SYNTH_ENDPOINT = env("TTS_SYNTH_ENDPOINT", default="/v1/tts")
TTS_TIMEOUT = env.int("TTS_TIMEOUT", default=60)
TTS_CLONE_TIMEOUT_SECONDS = env.int("TTS_CLONE_TIMEOUT_SECONDS", default=120)
TTS_CLONE_MAX_BYTES = env.int("TTS_CLONE_MAX_BYTES", default=15 * 1024 * 1024)
TTS_CLONE_POLL_TIMEOUT_SECONDS = env.int("TTS_CLONE_POLL_TIMEOUT_SECONDS", default=90)
TTS_CLONE_POLL_INTERVAL_SECONDS = env.int("TTS_CLONE_POLL_INTERVAL_SECONDS", default=2)
TTS_CLONE_ALLOWED_EXTENSIONS = env.list(
    "TTS_CLONE_ALLOWED_EXTENSIONS", default=["wav", "webm", "mp3", "m4a"]
)

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Legacy Chatterbox multilingual (RunPod) — deprecated, kept for env migration only:
CHATTERBOX_TTS_URL = env("CHATTERBOX_TTS_URL", default="")
VOICE_AGENT_CHATTERBOX_VOICE_ID = env("VOICE_AGENT_CHATTERBOX_VOICE_ID", default="")
# VOICE_AGENT_CHATTERBOX_OUTPUT_FORMAT = wav
# VOICE_AGENT_CHATTERBOX_CHUNK_SIZE — text chars on Chatterbox server, not HTTP bytes
# VOICE_AGENT_CHATTERBOX_SPLIT_TEXT / STREAM — Chatterbox-only streaming API
VOICE_AGENT_NAME = env("VOICE_AGENT_NAME", default="voice-agent")
VOICE_AGENT_SKIP_ROOM_SETUP = env.bool("VOICE_AGENT_SKIP_ROOM_SETUP", default=False)
VOICE_AGENT_SKIP_TTS_WARMUP = env.bool("VOICE_AGENT_SKIP_TTS_WARMUP", default=False)

# File logging is configured in apps.calls.apps.CallsConfig.ready() via config.logging_setup.
# Logs are cleared on each API/worker process start:
#   logs/voice_agent_api.log
#   logs/voice_agent_worker.log

(BASE_DIR / "logs").mkdir(exist_ok=True)

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"null": {"class": "logging.NullHandler"}},
    "root": {"handlers": ["null"], "level": "WARNING"},
}
