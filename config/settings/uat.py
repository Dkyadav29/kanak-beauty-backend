"""
UAT settings: production hardening without TLS requirements.

Use on a staging/UAT host that is reached over plain HTTP (e.g. a bare EC2 IP).
Keeps DEBUG off, strict hosts, JSON-only rendering and strong secrets, but
allows the sandbox payment gateway and does not force HTTPS redirects or
secure-only cookies, which would break every request without a certificate.
Switch to config.settings.production once a domain and TLS are in place.
"""

from .base import *  # noqa: F401,F403
from .base import config

DEBUG = False

if config("DJANGO_SECRET_KEY", default="") in ("", "insecure-dev-key-change-me"):
    raise RuntimeError("DJANGO_SECRET_KEY must be set to a strong unique value in UAT.")
if config("PAYMENT_WEBHOOK_SECRET", default="change-me-webhook-secret") == "change-me-webhook-secret":
    raise RuntimeError("PAYMENT_WEBHOOK_SECRET must be set in UAT.")

# Behind nginx on plain HTTP: no redirect, no secure-only cookies, no HSTS.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
_HTTPS = config("SECURE_SSL_REDIRECT", default=False, cast=bool)
SECURE_SSL_REDIRECT = _HTTPS
SESSION_COOKIE_SECURE = _HTTPS
CSRF_COOKIE_SECURE = _HTTPS
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_HSTS_SECONDS = 0
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
