import pytest

pytestmark = pytest.mark.django_db

URL = "/api/v1/student/profile"
EXPECTED_KEYS = {
    "id",
    "name",
    "email",
    "mobile",
    "role",
    "permissions",
    "avatarUrl",
    "emailVerified",
    "createdAt",
    "dateOfBirth",
    "address",
    "notificationPreferences",
}


def test_profile_get_success(student_client, student):
    res = student_client.get(URL)
    assert res.status_code == 200, res.content
    body = res.json()
    assert set(body) == EXPECTED_KEYS
    assert body["id"] == student.pk
    assert body["dateOfBirth"] is None and body["address"] is None and body["avatarUrl"] is None
    assert body["notificationPreferences"] == {
        "liveClassReminders": True,
        "courseUpdates": True,
        "paymentReceipts": True,
        "marketing": False,
    }
    assert "password" not in res.content.decode().lower()


def test_profile_requires_auth(api):
    assert api.get(URL).status_code == 401
    assert api.patch(URL, {"name": "X Y"}, format="json").status_code == 401


def test_profile_admin_forbidden(admin_client):
    assert admin_client.get(URL).status_code == 403
    res = admin_client.patch(URL, {"name": "Someone Else"}, format="json")
    assert res.status_code == 403
    assert res.json()["code"] == "FORBIDDEN"


def test_profile_patch_updates_fields(student_client, student):
    payload = {
        "name": "Priya S.",
        "mobile": "+91 90000 11111",
        "dateOfBirth": "1998-04-12",
        "address": "12 MG Road, Bengaluru",
        "avatarUrl": "https://cdn.example.test/avatars/priya.png",
        "notificationPreferences": {"marketing": True},
    }
    res = student_client.patch(URL, payload, format="json")
    assert res.status_code == 200, res.content
    body = res.json()
    assert body["name"] == "Priya S."
    assert body["mobile"] == "+91 90000 11111"
    assert body["dateOfBirth"] == "1998-04-12"
    assert body["address"] == "12 MG Road, Bengaluru"
    assert body["avatarUrl"] == payload["avatarUrl"]
    # Partial preference merge: untouched keys keep their values.
    assert body["notificationPreferences"] == {
        "liveClassReminders": True,
        "courseUpdates": True,
        "paymentReceipts": True,
        "marketing": True,
    }
    student.refresh_from_db()
    assert student.notify_marketing is True and str(student.date_of_birth) == "1998-04-12"


def test_profile_patch_accepts_data_url_avatar_and_clears_with_null(student_client, student):
    data_url = "data:image/png;base64," + "A" * 4000
    res = student_client.patch(URL, {"avatarUrl": data_url}, format="json")
    assert res.status_code == 200
    assert res.json()["avatarUrl"] == data_url
    res = student_client.patch(URL, {"avatarUrl": None, "address": None, "dateOfBirth": None}, format="json")
    assert res.status_code == 200
    body = res.json()
    assert body["avatarUrl"] is None and body["address"] is None and body["dateOfBirth"] is None


def test_profile_patch_rejects_oversized_data_url(student_client):
    data_url = "data:image/png;base64," + "A" * 400_001
    res = student_client.patch(URL, {"avatarUrl": data_url}, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
    assert "avatarUrl" in res.json()["details"]


@pytest.mark.parametrize(
    "bad", ["http://insecure.example/avatar.png", "javascript:alert(1)", "data:text/html;base64,AAAA"]
)
def test_profile_patch_rejects_non_https_non_image_avatar(student_client, bad):
    res = student_client.patch(URL, {"avatarUrl": bad}, format="json")
    assert res.status_code == 400
    assert "avatarUrl" in res.json()["details"]


def test_profile_patch_validation_errors(student_client):
    res = student_client.patch(URL, {"name": "A", "mobile": "12", "dateOfBirth": "yesterday"}, format="json")
    assert res.status_code == 400
    details = res.json()["details"]
    assert {"name", "mobile", "dateOfBirth"} <= set(details)


def test_profile_patch_ignores_email_and_role(student_client, student):
    res = student_client.patch(
        URL, {"email": "hacker@evil.test", "role": "admin", "name": "Priya Sharma"}, format="json"
    )
    assert res.status_code == 200
    student.refresh_from_db()
    assert student.email == "priya@student.test"
    assert student.role == "student"
    assert res.json()["email"] == "priya@student.test" and res.json()["role"] == "student"


def test_profile_patch_only_touches_own_record(student_client, other_student):
    before = other_student.name
    assert student_client.patch(URL, {"name": "Changed Name"}, format="json").status_code == 200
    other_student.refresh_from_db()
    assert other_student.name == before
