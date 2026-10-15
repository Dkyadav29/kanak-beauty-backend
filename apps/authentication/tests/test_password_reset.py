from datetime import timedelta

import pytest
from django.utils import timezone

from apps.accounts.models import PasswordResetToken
from apps.core.models import AuditLog
from conftest import STUDENT_PASSWORD

from .conftest import FORGOT_URL, REFRESH_URL, RESET_URL

pytestmark = pytest.mark.django_db

NEW_PASSWORD = "Fresh@2024"


def test_forgot_password_always_200_no_enumeration(api, student, mailoutbox):
    known = api.post(FORGOT_URL, {"email": student.email}, format="json")
    unknown = api.post(FORGOT_URL, {"email": "ghost@student.test"}, format="json")
    assert known.status_code == unknown.status_code == 200
    assert known.json() == unknown.json() == {"message": known.json()["message"]}
    assert "devResetToken" not in known.json()  # DEBUG is off in tests
    assert len(mailoutbox) == 1 and mailoutbox[0].to == [student.email]
    assert "/reset-password?token=" in mailoutbox[0].body
    row = PasswordResetToken.objects.get(user=student)
    assert row.token_hash not in mailoutbox[0].body  # only the hash is stored


def test_forgot_password_dev_token_only_in_debug(settings, api, student, reset_token_from_mail):
    settings.DEBUG = True
    res = api.post(FORGOT_URL, {"email": student.email}, format="json")
    assert res.status_code == 200
    assert res.json()["devResetToken"] == reset_token_from_mail(student.email)
    assert "devResetToken" not in api.post(FORGOT_URL, {"email": "ghost@student.test"}, format="json").json()


def test_forgot_password_invalidates_previous_token(api, student, reset_token_from_mail):
    api.post(FORGOT_URL, {"email": student.email}, format="json")
    first = reset_token_from_mail(student.email)
    api.post(FORGOT_URL, {"email": student.email}, format="json")
    assert PasswordResetToken.objects.filter(user=student, used_at__isnull=True).count() == 1
    res = api.post(RESET_URL, {"token": first, "password": NEW_PASSWORD}, format="json")
    assert res.status_code == 400 and res.json()["code"] == "INVALID_TOKEN"


def test_reset_password_flow_works_once_and_revokes_sessions(api, login, student, reset_token_from_mail):
    session = login(student.email, STUDENT_PASSWORD).json()
    api.post(FORGOT_URL, {"email": student.email}, format="json")
    token = reset_token_from_mail(student.email)

    res = api.post(RESET_URL, {"token": token, "password": NEW_PASSWORD}, format="json")
    assert res.status_code == 200, res.content
    assert "message" in res.json()

    assert login(student.email, STUDENT_PASSWORD).status_code == 401  # old password rejected
    assert login(student.email, NEW_PASSWORD).status_code == 200
    # Pre-reset refresh token no longer works.
    assert api.post(REFRESH_URL, {"refresh": session["refreshToken"]}, format="json").status_code == 401
    # Token is single use.
    again = api.post(RESET_URL, {"token": token, "password": "Another@456"}, format="json")
    assert again.status_code == 400 and again.json()["code"] == "INVALID_TOKEN"
    assert PasswordResetToken.objects.get(user=student).used_at is not None
    assert AuditLog.objects.filter(action="auth.password_reset", entity_id=student.pk).exists()


def test_reset_password_unknown_token_400(api):
    res = api.post(RESET_URL, {"token": "bogus", "password": NEW_PASSWORD}, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_TOKEN"


def test_reset_password_expired_token_400(api, student, reset_token_from_mail):
    api.post(FORGOT_URL, {"email": student.email}, format="json")
    token = reset_token_from_mail(student.email)
    PasswordResetToken.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    res = api.post(RESET_URL, {"token": token, "password": NEW_PASSWORD}, format="json")
    assert res.status_code == 400 and res.json()["code"] == "INVALID_TOKEN"


def test_reset_password_weak_password_400_keeps_token_usable(api, login, student, reset_token_from_mail):
    api.post(FORGOT_URL, {"email": student.email}, format="json")
    token = reset_token_from_mail(student.email)
    res = api.post(RESET_URL, {"token": token, "password": "weak"}, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
    assert "password" in res.json()["details"]
    assert login(student.email, STUDENT_PASSWORD).status_code == 200  # unchanged
    assert api.post(RESET_URL, {"token": token, "password": NEW_PASSWORD}, format="json").status_code == 200


def test_reset_password_marks_email_verified(api, unverified_student, reset_token_from_mail):
    api.post(FORGOT_URL, {"email": unverified_student.email}, format="json")
    token = reset_token_from_mail(unverified_student.email)
    assert api.post(RESET_URL, {"token": token, "password": NEW_PASSWORD}, format="json").status_code == 200
    unverified_student.refresh_from_db()
    assert unverified_student.email_verified is True
