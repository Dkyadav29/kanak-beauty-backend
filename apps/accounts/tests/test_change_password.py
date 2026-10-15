import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken

from apps.core.models import AuditLog
from conftest import STUDENT_PASSWORD

pytestmark = pytest.mark.django_db

URL = "/api/v1/student/change-password"
LOGIN_URL = "/api/v1/auth/login"
REFRESH_URL = "/api/v1/auth/token/refresh"
NEW_PASSWORD = "Updated@2024"


def _login(email, password):
    res = APIClient().post(LOGIN_URL, {"email": email, "password": password}, format="json")
    assert res.status_code == 200, res.content
    return res.json()


def test_change_password_success(student_client, student):
    res = student_client.post(URL, {"currentPassword": STUDENT_PASSWORD, "newPassword": NEW_PASSWORD}, format="json")
    assert res.status_code == 200, res.content
    assert res.json() == {"message": "Password updated."}
    student.refresh_from_db()
    assert student.check_password(NEW_PASSWORD)
    assert AuditLog.objects.filter(action="auth.password_change", entity_id=student.pk).exists()
    assert NEW_PASSWORD not in res.content.decode()


def test_change_password_wrong_current_400(student_client, student):
    res = student_client.post(URL, {"currentPassword": "Nope@123", "newPassword": NEW_PASSWORD}, format="json")
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "INVALID_PASSWORD"
    assert "currentPassword" in body["details"]
    student.refresh_from_db()
    assert student.check_password(STUDENT_PASSWORD)


def test_change_password_weak_new_400(student_client):
    res = student_client.post(URL, {"currentPassword": STUDENT_PASSWORD, "newPassword": "weak"}, format="json")
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "VALIDATION"
    assert "newPassword" in body["details"]


def test_change_password_missing_fields_400(student_client):
    res = student_client.post(URL, {}, format="json")
    assert res.status_code == 400
    assert {"currentPassword", "newPassword"} <= set(res.json()["details"])


def test_change_password_requires_auth_and_student_role(api, admin_client):
    assert api.post(URL, {"currentPassword": "x", "newPassword": "y"}, format="json").status_code == 401
    assert admin_client.post(URL, {"currentPassword": "x", "newPassword": "y"}, format="json").status_code == 403


def test_change_password_revokes_other_sessions(student):
    older = _login(student.email, STUDENT_PASSWORD)
    current = _login(student.email, STUDENT_PASSWORD)
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {current['accessToken']}")
    res = client.post(URL, {"currentPassword": STUDENT_PASSWORD, "newPassword": NEW_PASSWORD}, format="json")
    assert res.status_code == 200
    assert BlacklistedToken.objects.filter(token__user=student).count() == 1
    anon = APIClient()
    assert anon.post(REFRESH_URL, {"refresh": older["refreshToken"]}, format="json").status_code == 401
    assert anon.post(REFRESH_URL, {"refresh": current["refreshToken"]}, format="json").status_code == 200
    # Old password is now rejected, the new one works.
    assert (
        APIClient().post(LOGIN_URL, {"email": student.email, "password": STUDENT_PASSWORD}, format="json").status_code
        == 401
    )
    _login(student.email, NEW_PASSWORD)
