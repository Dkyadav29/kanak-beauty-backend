"""Auth test helpers. The throttle cache is process-wide, so it is cleared around every test."""

from __future__ import annotations

import re

import pytest
from django.core.cache import cache

REGISTER_URL = "/api/v1/auth/register"
LOGIN_URL = "/api/v1/auth/login"
VERIFY_URL = "/api/v1/auth/verify-otp"
RESEND_URL = "/api/v1/auth/resend-otp"
LOGOUT_URL = "/api/v1/auth/logout"
REFRESH_URL = "/api/v1/auth/token/refresh"
ME_URL = "/api/v1/auth/me"
FORGOT_URL = "/api/v1/auth/forgot-password"
RESET_URL = "/api/v1/auth/reset-password"

OTP_RE = re.compile(r"\b(\d{6})\b")
RESET_TOKEN_RE = re.compile(r"token=([A-Za-z0-9_\-]+)")


@pytest.fixture(autouse=True)
def _clear_throttle_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def register_payload():
    return {
        "name": "Riya Kapoor",
        "email": "riya@student.test",
        "mobile": "+91 98765 43210",
        "password": "Secure@123",
    }


@pytest.fixture
def otp_from_mail(mailoutbox):
    """Return the most recent OTP emailed to ``email``."""

    def _get(email: str) -> str:
        for message in reversed(mailoutbox):
            if email in message.to:
                match = OTP_RE.search(message.body)
                assert match, "verification email has no 6-digit code"
                return match.group(1)
        raise AssertionError(f"no verification email sent to {email}")

    return _get


@pytest.fixture
def reset_token_from_mail(mailoutbox):
    def _get(email: str) -> str:
        for message in reversed(mailoutbox):
            if email in message.to:
                match = RESET_TOKEN_RE.search(message.body)
                assert match, "reset email has no token link"
                return match.group(1)
        raise AssertionError(f"no reset email sent to {email}")

    return _get


@pytest.fixture
def login(api):
    def _login(email: str, password: str):
        return api.post(LOGIN_URL, {"email": email, "password": password}, format="json")

    return _login
