"""Production settings: DEBUG off, strict hosts, HTTPS-only cookies, HSTS, JSON renderer only."""

from .base import *  # noqa: F401,F403
from .base import config

DEBUG = False

if config("DJANGO_SECRET_KEY", default="") in ("", "insecure-dev-key-change-me"):
    raise RuntimeError("DJANGO_SECRET_KEY must be set to a strong unique value in production.")
if config("PAYMENT_WEBHOOK_SECRET", default="change-me-webhook-secret") == "change-me-webhook-secret":
    raise RuntimeError("PAYMENT_WEBHOOK_SECRET must be set in production.")
if config("PAYMENT_GATEWAY", default="sandbox") == "sandbox":
    raise RuntimeError("PAYMENT_GATEWAY must not be 'sandbox' in production.")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = config("SECURE_SSL_REDIRECT", default=True, cast=bool)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
CSRF_COOKIE_SAMESITE = "Lax"
SECURE_HSTS_SECONDS = config("SECURE_HSTS_SECONDS", default=31536000, cast=int)
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
X_FRAME_OPTIONS = "DENY"
