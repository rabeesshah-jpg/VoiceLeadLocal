from .base import *  # noqa: F403

DEBUG = True

# Always use SQLite for local voice-agent API (avoid parent .env Postgres without psycopg).
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
    }
}
