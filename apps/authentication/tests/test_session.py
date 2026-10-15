"""/auth/me, /auth/logout and /auth/token/refresh."""

import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from conftest import STUDENT_PASSWORD

from .conftest import LOGOUT_URL, ME_URL, REFRESH_URL

pytestmark = pytest.mark.django_db


def _session(login, user):
    res = login(user.email, STUDENT_PASSWORD)
    assert res.status_code == 200
    return res.json()


def _bearer(token: str) -> APIClient:
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


def test_me_requires_auth(api):
    res = api.get(ME_URL)
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHENTICATED"


def test_me_rejects_garbage_token(api):
    api.credentials(HTTP_AUTHORIZATION="Bearer not-a-jwt")
    res = api.get(ME_URL)
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHENTICATED"


def test_me_returns_current_user(student_client, student):
    res = student_client.get(ME_URL)
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == student.pk
    assert body["email"] == student.email
    assert body["role"] == "student"
    assert body["permissions"] == student.permissions
    assert "password" not in body and "referralCode" not in body


def test_me_for_admin(admin_client, admin_user):
    body = admin_client.get(ME_URL).json()
    assert body["role"] == "admin" and "audit:read" in body["permissions"]


def test_logout_requires_auth(api):
    assert api.post(LOGOUT_URL, {}, format="json").status_code == 401


def test_logout_with_refresh_blacklists_it(login, student, api):
    session = _session(login, student)
    client = _bearer(session["accessToken"])
    res = client.post(LOGOUT_URL, {"refresh": session["refreshToken"]}, format="json")
    assert res.status_code == 200
    assert res.json() == {"success": True}
    assert BlacklistedToken.objects.count() == 1
    refreshed = api.post(REFRESH_URL, {"refresh": session["refreshToken"]}, format="json")
    assert refreshed.status_code == 401
    assert refreshed.json()["code"] == "UNAUTHENTICATED"


def test_logout_without_body_blacklists_all_outstanding_tokens(login, student, api):
    first = _session(login, student)
    second = _session(login, student)
    assert OutstandingToken.objects.filter(user=student).count() == 2
    res = _bearer(second["accessToken"]).post(LOGOUT_URL)
    assert res.status_code == 200
    assert BlacklistedToken.objects.filter(token__user=student).count() == 2
    for session in (first, second):
        assert api.post(REFRESH_URL, {"refresh": session["refreshToken"]}, format="json").status_code == 401


def test_logout_cannot_revoke_another_users_token(login, student, other_student):
    victim = _session(login, other_student)
    attacker = _session(login, student)
    res = _bearer(attacker["accessToken"]).post(LOGOUT_URL, {"refresh": victim["refreshToken"]}, format="json")
    assert res.status_code == 200
    assert not BlacklistedToken.objects.filter(token__user=other_student).exists()
    assert BlacklistedToken.objects.filter(token__user=student).exists()


def test_token_refresh_rotates_and_blacklists_old(login, student, api):
    session = _session(login, student)
    res = api.post(REFRESH_URL, {"refresh": session["refreshToken"]}, format="json")
    assert res.status_code == 200, res.content
    body = res.json()
    assert set(body) == {"access", "refresh", "expiresAt"}
    assert body["refresh"] != session["refreshToken"]
    assert _bearer(body["access"]).get(ME_URL).status_code == 200
    # Old refresh token is blacklisted after rotation; the new one works.
    assert api.post(REFRESH_URL, {"refresh": session["refreshToken"]}, format="json").status_code == 401
    assert api.post(REFRESH_URL, {"refresh": body["refresh"]}, format="json").status_code == 200


def test_token_refresh_invalid_token_401(api):
    res = api.post(REFRESH_URL, {"refresh": "nope"}, format="json")
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHENTICATED"


def test_token_refresh_missing_body_400(api):
    res = api.post(REFRESH_URL, {}, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
