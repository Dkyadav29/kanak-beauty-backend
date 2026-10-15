import pytest

pytestmark = pytest.mark.django_db

LIST = "/api/v1/student/certificates"


def test_student_lists_own_certificates_including_revoked(student_client, revoked_certificate, batch, student, course):
    res = student_client.get(LIST)
    assert res.status_code == 200
    items = res.json()
    assert isinstance(items, list) and len(items) == 1
    cert = items[0]
    assert cert["id"] == revoked_certificate.id
    assert cert["status"] == "revoked"
    assert cert["revokedAt"] is not None
    assert cert["revokedReason"] == "Issued in error: assessment not completed"
    assert cert["batchId"] == batch.id
    assert cert["studentId"] == student.id
    assert cert["courseId"] == course.id
    assert cert["verificationUrl"] == f"/verify/{revoked_certificate.certificate_number}"
    assert set(cert) == {
        "id",
        "certificateNumber",
        "studentId",
        "studentName",
        "courseId",
        "courseTitle",
        "batchId",
        "completionDate",
        "issuedAt",
        "issuedBy",
        "status",
        "revokedAt",
        "revokedReason",
        "verificationUrl",
    }


def test_issued_certificate_has_null_revocation_fields(student_client, certificate):
    cert = student_client.get(LIST).json()[0]
    assert cert["revokedAt"] is None and cert["revokedReason"] is None
    assert cert["status"] == "issued"


def test_student_detail_and_ownership(student_client, other_student_client, certificate):
    assert student_client.get(f"{LIST}/{certificate.id}").status_code == 200
    assert other_student_client.get(LIST).json() == []
    res = other_student_client.get(f"{LIST}/{certificate.id}")
    assert res.status_code == 404
    assert res.json()["code"] == "NOT_FOUND"


def test_student_endpoints_require_student_role(api, admin_client, certificate):
    assert api.get(LIST).status_code == 401
    assert api.get(LIST).json()["code"] == "UNAUTHENTICATED"
    assert admin_client.get(LIST).status_code == 403
    assert admin_client.get(f"{LIST}/{certificate.id}").status_code == 403
