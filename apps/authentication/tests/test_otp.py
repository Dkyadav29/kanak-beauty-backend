from datetime import timedelta

import pytest
from django.core.cache import cache
from django.utils import timezone

from apps.accounts.models import OneTimeCode, User

from .conftest import ME_URL, REGISTER_URL, RESEND_URL, VERIFY_URL

pytestmark = pytest.mark.django_db


@pytest.fixture
def registered(api, register_payload, otp_from_mail):
    res = api.post(REGISTER_URL, register_payload, format="json")
    assert res.status_code == 201
    return register_payload["email"], otp_from_mail(register_payload["email"])


def test_verify_otp_success_returns_session(api, registered):
    email, code = registered
    res = api.post(VERIFY_URL, {"email": email, "otp": code}, format="json")
    assert res.status_code == 200, res.content
    body = res.json()
    assert body["accessToken"] and body["refreshToken"] and body["expiresAt"]
    assert body["user"]["email"] == email
    assert body["user"]["role"] == "student"
    assert body["user"]["emailVerified"] is True
    assert "profile:read:self" in body["user"]["permissions"]
    assert code not in res.content.decode()
    assert User.objects.get(email=email).email_verified is True
    assert OneTimeCode.objects.filter(consumed_at__isnull=True).count() == 0
    # The access token is usable right away.
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {body['accessToken']}")
    assert api.get(ME_URL).status_code == 200


def test_verify_otp_wrong_code_400(api, registered):
    email, code = registered
    wrong = "000000" if code != "000000" else "111111"
    res = api.post(VERIFY_URL, {"email": email, "otp": wrong}, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_OTP"
    assert User.objects.get(email=email).email_verified is False
    assert OneTimeCode.objects.get(consumed_at__isnull=True).attempts == 1


def test_verify_otp_locks_after_five_wrong_attempts(api, registered):
    email, code = registered
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(5):
        assert api.post(VERIFY_URL, {"email": email, "otp": wrong}, format="json").status_code == 400
    assert OneTimeCode.objects.filter(consumed_at__isnull=True).count() == 0  # invalidated after 5 guesses
    # The OTP throttle (5/10m) would also answer 429 here; clear it to prove the code itself is dead.
    cache.clear()
    res = api.post(VERIFY_URL, {"email": email, "otp": code}, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_OTP"


def test_verify_otp_expired_400(api, registered):
    email, code = registered
    OneTimeCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    res = api.post(VERIFY_URL, {"email": email, "otp": code}, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_OTP"


def test_verify_otp_cannot_be_reused(api, registered):
    email, code = registered
    assert api.post(VERIFY_URL, {"email": email, "otp": code}, format="json").status_code == 200
    res = api.post(VERIFY_URL, {"email": email, "otp": code}, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_OTP"


def test_verify_otp_unknown_email_400(api):
    res = api.post(VERIFY_URL, {"email": "nobody@student.test", "otp": "123456"}, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_OTP"


def test_resend_otp_issues_new_code_and_invalidates_old(api, registered, otp_from_mail, mailoutbox):
    email, old_code = registered
    res = api.post(RESEND_URL, {"email": email}, format="json")
    assert res.status_code == 200
    assert "message" in res.json()
    assert len(mailoutbox) == 2
    new_code = otp_from_mail(email)
    assert OneTimeCode.objects.filter(consumed_at__isnull=True).count() == 1
    if new_code != old_code:  # astronomically likely
        assert api.post(VERIFY_URL, {"email": email, "otp": old_code}, format="json").status_code == 400
    assert api.post(VERIFY_URL, {"email": email, "otp": new_code}, format="json").status_code == 200


def test_resend_otp_always_200_without_enumeration(api, student, mailoutbox):
    unknown = api.post(RESEND_URL, {"email": "ghost@student.test"}, format="json")
    verified = api.post(RESEND_URL, {"email": student.email}, format="json")
    assert unknown.status_code == verified.status_code == 200
    assert unknown.json() == verified.json()
    assert len(mailoutbox) == 0  # nothing sent for unknown or already-verified accounts
