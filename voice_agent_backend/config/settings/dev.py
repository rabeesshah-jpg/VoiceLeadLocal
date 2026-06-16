import os

from .base import *  # noqa: F403

DEBUG = True

# Local dev: SQLite when DATABASE_URL is unset; cloud Postgres when DATABASE_URL is set.
if not (os.environ.get("DATABASE_URL") or "").strip():
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",  # noqa: F405
        }
    }
