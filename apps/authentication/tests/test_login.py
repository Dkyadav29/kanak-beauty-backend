import pytest

from apps.accounts.models import LoginAttempt
from conftest import ADMIN_PASSWORD, STUDENT_PASSWORD

pytestmark = pytest.mark.django_db


def test_login_student_success(login, student):
    res = login(student.email, STUDENT_PASSWORD)
    assert res.status_code == 200, res.content
    body = res.json()
    assert set(body) == {"accessToken", "refreshToken", "expiresAt", "user"}
    user = body["user"]
    assert user["id"] == student.pk
    assert user["role"] == "student"
    assert user["permissions"] == student.permissions
    assert user["avatarUrl"] is None
    assert user["emailVerified"] is True
    assert set(user) == {
        "id",
        "name",
        "email",
        "mobile",
        "role",
        "permissions",
        "avatarUrl",
        "emailVerified",
        "createdAt",
    }
    assert STUDENT_PASSWORD not in res.content.decode()


def test_login_admin_role_and_permissions(login, admin_user):
    res = login(admin_user.email, ADMIN_PASSWORD)
    assert res.status_code == 200
    user = res.json()["user"]
    assert user["role"] == "admin"
    assert "audit:read" in user["permissions"]
    assert "profile:read:self" not in user["permissions"]


def test_login_email_is_case_insensitive(login, student):
    assert login(student.email.upper(), STUDENT_PASSWORD).status_code == 200


def test_login_bad_credentials_401(login, student):
    res = login(student.email, "Wrong@123")
    assert res.status_code == 401
    assert res.json()["code"] == "INVALID_CREDENTIALS"
    unknown = login("ghost@student.test", "Wrong@123")
    assert unknown.status_code == 401
    assert unknown.json() == res.json()  # identical response: no account enumeration


def test_login_unverified_403(login, unverified_student):
    res = login(unverified_student.email, STUDENT_PASSWORD)
    assert res.status_code == 403
    assert res.json()["code"] == "EMAIL_NOT_VERIFIED"


def test_login_disabled_403(login, student):
    student.is_active = False
    student.save(update_fields=["is_active"])
    res = login(student.email, STUDENT_PASSWORD)
    assert res.status_code == 403
    assert res.json()["code"] == "ACCOUNT_DISABLED"


def test_login_validation_400(api):
    res = api.post("/api/v1/auth/login", {"email": "not-an-email"}, format="json")
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "VALIDATION"
    assert {"email", "password"} <= set(body["details"])


def test_login_writes_login_attempt_rows_without_password(login, student):
    assert login(student.email, "Wrong@123").status_code == 401
    assert login(student.email, STUDENT_PASSWORD).status_code == 200
    rows = list(LoginAttempt.objects.filter(email=student.email).order_by("id"))
    assert [r.successful for r in rows] == [False, True]
    assert rows[0].reason == "invalid_credentials"
    assert rows[0].ip_address == "127.0.0.1"
    dump = str([vars(r) for r in rows])
    assert STUDENT_PASSWORD not in dump and "Wrong@123" not in dump


def test_login_rate_limited_after_five_failures(settings, login, student):
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {**settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "auth_login": "5/10m"},
    }
    for _ in range(5):
        assert login(student.email, "Wrong@123").status_code == 401
    res = login(student.email, STUDENT_PASSWORD)
    assert res.status_code == 429
    assert res.json()["code"] == "RATE_LIMITED"
    assert "Retry-After" in res.headers
    # A different identifier is not affected.
    assert login("someone-else@student.test", "Wrong@123").status_code == 401


def test_successful_login_resets_failed_attempt_counter(settings, login, student):
    settings.REST_FRAMEWORK = {
        **settings.REST_FRAMEWORK,
        "DEFAULT_THROTTLE_RATES": {**settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "auth_login": "5/10m"},
    }
    for _ in range(4):
        assert login(student.email, "Wrong@123").status_code == 401
    assert login(student.email, STUDENT_PASSWORD).status_code == 200
    for _ in range(4):
        assert login(student.email, "Wrong@123").status_code == 401
    assert login(student.email, STUDENT_PASSWORD).status_code == 200
