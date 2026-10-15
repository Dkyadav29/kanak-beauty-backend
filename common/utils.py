"""Small shared helpers for views and services."""

from __future__ import annotations

import re
from typing import Any

from django.conf import settings
from django.utils import timezone

_CAMEL_RE = re.compile(r"([a-z0-9])([A-Z])")


def snake(name: str) -> str:
    return _CAMEL_RE.sub(r"\1_\2", name).lower()


def query_param(request, camel_name: str, default: Any = None) -> Any:
    """
    Read a query parameter by its camelCase (frontend) name, also accepting the
    snake_case spelling. Empty strings and the literal 'all' are treated as absent.
    """
    value = request.query_params.get(camel_name)
    if value is None:
        value = request.query_params.get(snake(camel_name))
    if value is None or value == "" or value == "all":
        return default
    return value


def query_bool(request, camel_name: str, default: bool | None = None) -> bool | None:
    value = query_param(request, camel_name)
    if value is None:
        return default
    return str(value).lower() in ("1", "true", "yes")


def public_origin(request) -> str:
    """Origin used to build absolute share/verification links."""
    header = request.headers.get("X-Public-Origin", "") if request is not None else ""
    origin = header.strip() or settings.PUBLIC_SITE_URL
    return origin.rstrip("/")


def now_iso() -> str:
    return timezone.now().isoformat()


SENSITIVE_KEYS = {
    "password",
    "new_password",
    "current_password",
    "otp",
    "token",
    "access",
    "refresh",
    "secret",
    "authorization",
}


def redact(data: Any) -> Any:
    """Recursively mask sensitive values before logging."""
    if isinstance(data, dict):
        return {k: ("***" if str(k).lower() in SENSITIVE_KEYS else redact(v)) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [redact(v) for v in data]
    return data


def mask_email(email: str) -> str:
    if "@" not in email:
        return email
    name, domain = email.split("@", 1)
    return f"{name[:2]}•••@{domain}"
