import re

import pytest
from django.conf import settings

from apps.batches.models import BatchHistoryEvent
from apps.certificates.models import Certificate
from apps.core.models import AuditLog
from apps.enrollments.models import CourseEnrollment

pytestmark = pytest.mark.django_db

BASE = "/api/v1/admin/certificates"
NUMBER_RE = re.compile(rf"^{settings.CERTIFICATE_NUMBER_PREFIX}-\d{{4}}-\d{{5}}$")


# ---------------------------------------------------------------------------
# Listing / detail
# ---------------------------------------------------------------------------
def test_list_is_paginated_and_filterable(
    admin_client, certificate, other_enrollment, other_student, recorded_course, eligible
):
    other = admin_client.post(
        f"{BASE}/issue", {"studentId": other_student.id, "courseId": recorded_course.id}, format="json"
    )
    assert other.status_code == 201

    res = admin_client.get(BASE)
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"items", "total", "page", "pageSize"}
    assert body["total"] == 2 and body["page"] == 1 and body["pageSize"] == 10
    assert body["items"][0]["issuedAt"] >= body["items"][1]["issuedAt"]

    assert admin_client.get(BASE, {"search": "ananya"}).json()["total"] == 1
    assert admin_client.get(BASE, {"search": certificate.certificate_number.lower()}).json()["total"] == 1
    assert admin_client.get(BASE, {"search": "Salon Management"}).json()["total"] == 1
    assert admin_client.get(BASE, {"courseId": recorded_course.id}).json()["items"][0]["studentId"] == other_student.id
    assert admin_client.get(BASE, {"status": "revoked"}).json()["total"] == 0
    assert admin_client.get(BASE, {"status": "all"}).json()["total"] == 2
    page2 = admin_client.get(BASE, {"page": 2, "pageSize": 1}).json()
    assert page2["total"] == 2 and len(page2["items"]) == 1 and page2["pageSize"] == 1


def test_detail(admin_client, certificate):
    res = admin_client.get(f"{BASE}/{certificate.id}")
    assert res.status_code == 200
    assert res.json()["certificateNumber"] == certificate.certificate_number
    assert admin_client.get(f"{BASE}/cert_missing").status_code == 404


# ---------------------------------------------------------------------------
# Eligible list
# ---------------------------------------------------------------------------
def test_eligible_list(admin_client, enrollment, student, course, batch, eligible):
    res = admin_client.get(f"{BASE}/eligible/list")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["studentId"] == student.id
    assert row["studentEmail"] == student.email
    assert row["courseId"] == course.id
    assert row["courseTitle"] == course.title
    assert row["batchId"] == batch.id
    assert row["completedAt"]
    assert row["evidence"] == {
        "requiredLessonsCompleted": "4/4",
        "liveAttendancePercent": 100,
        "assessmentPassed": True,
    }
    assert eligible == [(student.id, course.id)]


def test_eligible_list_excludes_ineligible_issued_and_disabled(
    admin_client, enrollment, other_enrollment, recorded_course, ineligible
):
    assert admin_client.get(f"{BASE}/eligible/list").json() == []


def test_eligible_list_excludes_students_with_certificate_or_disabled_course(
    admin_client, certificate, other_enrollment, recorded_course, eligible
):
    rows = admin_client.get(f"{BASE}/eligible/list").json()
    assert [r["courseId"] for r in rows] == [recorded_course.id]
    recorded_course.certificate_enabled = False
    recorded_course.save(update_fields=["certificate_enabled"])
    assert admin_client.get(f"{BASE}/eligible/list").json() == []


# ---------------------------------------------------------------------------
# Issue
# ---------------------------------------------------------------------------
def test_issue_eligible_student(admin_client, admin_user, enrollment, student, course, batch, eligible):
    res = admin_client.post(f"{BASE}/issue", {"studentId": student.id, "courseId": course.id}, format="json")
    assert res.status_code == 201, res.json()
    body = res.json()
    assert NUMBER_RE.match(body["certificateNumber"])
    assert body["certificateNumber"].endswith("-00201")
    assert body["status"] == "issued"
    assert body["issuedBy"] == admin_user.name
    assert body["batchId"] == batch.id
    assert body["studentName"] == student.name and body["courseTitle"] == course.title
    assert body["verificationUrl"] == f"/verify/{body['certificateNumber']}"

    cert = Certificate.objects.get(pk=body["id"])
    assert cert.issued_by_user == admin_user
    enrollment.refresh_from_db()
    assert enrollment.completed_at is not None
    assert cert.completion_date == enrollment.completed_at
    audit = AuditLog.objects.get(action="certificate.issue")
    assert audit.actor == admin_user and audit.entity_id == cert.id
    assert BatchHistoryEvent.objects.filter(batch=batch, type="certificate_issued").exists()


def test_issue_not_eligible(admin_client, enrollment, student, course, ineligible):
    res = admin_client.post(f"{BASE}/issue", {"studentId": student.id, "courseId": course.id}, format="json")
    assert res.status_code == 409
    assert res.json()["code"] == "NOT_ELIGIBLE"
    assert Certificate.objects.count() == 0


def test_issue_not_enrolled(admin_client, other_student, course, eligible):
    res = admin_client.post(f"{BASE}/issue", {"studentId": other_student.id, "courseId": course.id}, format="json")
    assert res.status_code == 409
    assert res.json()["code"] == "NOT_ENROLLED"


def test_issue_requires_active_enrollment(admin_client, enrollment, student, course, eligible):
    CourseEnrollment.objects.filter(pk=enrollment.pk).update(access_status="suspended")
    res = admin_client.post(f"{BASE}/issue", {"studentId": student.id, "courseId": course.id}, format="json")
    assert res.status_code == 409 and res.json()["code"] == "NOT_ENROLLED"


def test_issue_not_enabled(admin_client, enrollment, student, course, eligible):
    course.certificate_enabled = False
    course.save(update_fields=["certificate_enabled"])
    res = admin_client.post(f"{BASE}/issue", {"studentId": student.id, "courseId": course.id}, format="json")
    assert res.status_code == 409 and res.json()["code"] == "NOT_ENABLED"


def test_issue_already_issued(admin_client, certificate, student, course, eligible):
    res = admin_client.post(f"{BASE}/issue", {"studentId": student.id, "courseId": course.id}, format="json")
    assert res.status_code == 409
    assert res.json()["code"] == "ALREADY_ISSUED"
    assert Certificate.objects.count() == 1


def test_issue_validation_and_unknown_ids(admin_client, enrollment, student, course, eligible):
    res = admin_client.post(f"{BASE}/issue", {"studentId": student.id}, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
    assert "courseId" in res.json()["details"]
    assert (
        admin_client.post(f"{BASE}/issue", {"studentId": "usr_nope", "courseId": course.id}, format="json").status_code
        == 404
    )
    assert (
        admin_client.post(f"{BASE}/issue", {"studentId": student.id, "courseId": "crs_nope"}, format="json").status_code
        == 404
    )


def test_numbers_are_unique_and_sequential_across_issues(
    admin_client, enrollment, student, course, other_enrollment, other_student, recorded_course, eligible
):
    first = admin_client.post(f"{BASE}/issue", {"studentId": student.id, "courseId": course.id}, format="json").json()
    second = admin_client.post(
        f"{BASE}/issue", {"studentId": other_student.id, "courseId": recorded_course.id}, format="json"
    ).json()
    assert first["certificateNumber"] != second["certificateNumber"]
    assert int(first["certificateNumber"].rsplit("-", 1)[1]) + 1 == int(second["certificateNumber"].rsplit("-", 1)[1])
    assert Certificate.objects.values("certificate_number").distinct().count() == 2


# ---------------------------------------------------------------------------
# Revoke
# ---------------------------------------------------------------------------
def test_revoke(admin_client, admin_user, certificate, batch):
    res = admin_client.post(f"{BASE}/{certificate.id}/revoke", {"reason": "Plagiarised portfolio"}, format="json")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "revoked"
    assert body["revokedAt"] is not None
    assert body["revokedReason"] == "Plagiarised portfolio"
    certificate.refresh_from_db()
    assert certificate.revoked_by == admin_user
    audit = AuditLog.objects.get(action="certificate.revoke")
    assert audit.actor == admin_user and audit.entity_id == certificate.id and "Plagiarised portfolio" in audit.details
    history = BatchHistoryEvent.objects.get(batch=batch, type="certificate_revoked")
    assert certificate.certificate_number in history.description
    assert history.actor == admin_user

    again = admin_client.post(f"{BASE}/{certificate.id}/revoke", {"reason": "Plagiarised portfolio"}, format="json")
    assert again.status_code == 409 and again.json()["code"] == "ALREADY_REVOKED"


def test_revoke_reason_validation(admin_client, certificate):
    res = admin_client.post(f"{BASE}/{certificate.id}/revoke", {"reason": "bad"}, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION" and "reason" in res.json()["details"]
    assert admin_client.post(f"{BASE}/{certificate.id}/revoke", {}, format="json").status_code == 400
    certificate.refresh_from_db()
    assert certificate.status == "issued"
    assert admin_client.post(f"{BASE}/cert_missing/revoke", {"reason": "Long enough"}, format="json").status_code == 404


# ---------------------------------------------------------------------------
# Reissue
# ---------------------------------------------------------------------------
def test_reissue_only_revoked(admin_client, certificate, eligible):
    res = admin_client.post(f"{BASE}/{certificate.id}/reissue", {"reason": "Name correction"}, format="json")
    assert res.status_code == 409 and res.json()["code"] == "NOT_REVOKED"


def test_reissue_revoked_certificate(admin_client, admin_user, revoked_certificate, eligible):
    res = admin_client.post(
        f"{BASE}/{revoked_certificate.id}/reissue", {"reason": "Assessment now completed"}, format="json"
    )
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["id"] != revoked_certificate.id
    assert body["certificateNumber"] != revoked_certificate.certificate_number
    assert NUMBER_RE.match(body["certificateNumber"])
    assert body["status"] == "issued"
    assert body["issuedBy"] == admin_user.name
    new = Certificate.objects.get(pk=body["id"])
    assert new.reissued_from == revoked_certificate
    assert Certificate.objects.filter(status="issued").count() == 1
    audit = AuditLog.objects.get(action="certificate.reissue")
    assert revoked_certificate.certificate_number in audit.details and audit.entity_id == new.id
    # The fixture's original issuance wrote one certificate.issue row; the reissue must not add another.
    assert AuditLog.objects.filter(action="certificate.issue").count() == 1


def test_reissue_requires_eligibility(admin_client, revoked_certificate, ineligible):
    res = admin_client.post(f"{BASE}/{revoked_certificate.id}/reissue", {"reason": "Please reissue"}, format="json")
    assert res.status_code == 409 and res.json()["code"] == "NOT_ELIGIBLE"
    assert Certificate.objects.count() == 1


def test_reissue_reason_validation(admin_client, revoked_certificate, eligible):
    res = admin_client.post(f"{BASE}/{revoked_certificate.id}/reissue", {"reason": "x"}, format="json")
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "method,path,payload",
    [
        ("get", BASE, None),
        ("get", f"{BASE}/eligible/list", None),
        ("get", f"{BASE}/{{id}}", None),
        ("post", f"{BASE}/issue", {"studentId": "x", "courseId": "y"}),
        ("post", f"{BASE}/{{id}}/revoke", {"reason": "Long enough reason"}),
        ("post", f"{BASE}/{{id}}/reissue", {"reason": "Long enough reason"}),
    ],
)
def test_admin_endpoints_reject_students_and_anonymous(api, student_client, certificate, method, path, payload):
    url = path.format(id=certificate.id)
    anon = getattr(api, method)(url, payload, format="json")
    assert anon.status_code == 401
    assert anon.json()["code"] == "UNAUTHENTICATED"
    forbidden = getattr(student_client, method)(url, payload, format="json")
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "FORBIDDEN"
