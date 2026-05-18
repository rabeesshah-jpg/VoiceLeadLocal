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
DEEPGRAM_API_KEY = env("DEEPGRAM_API_KEY", default="")
OPENROUTER_API_KEY = env("OPENROUTER_API_KEY", default="")
OPENROUTER_BASE_URL = env("OPENROUTER_BASE_URL", default="https://openrouter.ai/api/v1")
VOICE_AGENT_LLM_MODEL = env("VOICE_AGENT_LLM_MODEL", default="openai/gpt-4o-mini")
CARTESIA_API_KEY = env("CARTESIA_API_KEY", default="")
VOICE_AGENT_CARTESIA_VOICE_ID = env(
    "VOICE_AGENT_CARTESIA_VOICE_ID",
    default="0ad65e7f-006c-47cf-bd31-52279d487913",
)
VOICE_AGENT_SYSTEM_PROMPT = env(
    "VOICE_AGENT_SYSTEM_PROMPT",
    default="You are a helpful voice assistant. Keep replies concise and conversational.",
)
VOICE_AGENT_NAME = env("VOICE_AGENT_NAME", default="voice-agent")
VOICE_AGENT_SKIP_ROOM_SETUP = env.bool("VOICE_AGENT_SKIP_ROOM_SETUP", default=False)

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
