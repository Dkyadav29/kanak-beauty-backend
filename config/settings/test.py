"""
Test settings.

Tests run against MariaDB (the required database) whenever DB credentials are
configured: the test runner creates DB_TEST_NAME (default test_kanak_academy),
so the DB user needs CREATE/DROP on that database (see scripts/mariadb_setup.sql).

Set TEST_DB_SQLITE=1 only for quick local iteration when MariaDB is not reachable.
This fallback is never used by the application itself.
"""

from .base import *  # noqa: F401,F403
from .base import BASE_DIR, DATABASES, REST_FRAMEWORK, config

DEBUG = False
PAYMENT_GATEWAY = "sandbox"
PAYMENT_WEBHOOK_SECRET = "test-webhook-secret"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]  # fast hashing for tests only

if config("TEST_DB_SQLITE", default=False, cast=bool):
    # In-memory so parallel test runs never share state on disk.
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:",
            "TEST": {"NAME": ":memory:"},
        }
    }
    _ = BASE_DIR
else:
    DATABASES = DATABASES

# Throttling is covered by dedicated tests that enable it explicitly.
REST_FRAMEWORK = {
    **REST_FRAMEWORK,
    "DEFAULT_THROTTLE_RATES": {**REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "user": "100000/min", "anon": "100000/min"},
}
