"""Admin enrollment endpoints: list filters/pagination, grant, access changes, audit rows."""

from __future__ import annotations

import pytest

from apps.core.models import AuditLog
from apps.enrollments.models import CourseEnrollment

pytestmark = pytest.mark.django_db

URL = "/api/v1/admin/enrollments"


@pytest.fixture
def second_enrollment(db, other_student, recorded_course):
    return CourseEnrollment.objects.create(
        student=other_student, course=recorded_course, source="admin_grant", access_status="suspended"
    )


def test_list_rows_and_shape(admin_client, enrollment, second_enrollment, batch, course):
    res = admin_client.get(URL)
    assert res.status_code == 200
    body = res.json()
    assert set(body) == {"items", "total", "page", "pageSize"}
    assert body["total"] == 2
    assert body["page"] == 1 and body["pageSize"] == 10
    rows = {r["enrollmentId"]: r for r in body["items"]}
    paid = rows[enrollment.id]
    assert paid["studentName"] == enrollment.student.name
    assert paid["studentEmail"] == enrollment.student.email
    assert paid["courseTitle"] == course.title
    assert paid["batchId"] == batch.id and paid["batchName"] == batch.name
    assert paid["orderId"] == "KBA-ORD-1001"
    assert paid["amount"] == float(course.effective_price)
    assert paid["currency"] == "INR"
    assert paid["paymentStatus"] == "paid"
    assert paid["refundStatus"] == "none"
    assert paid["accessStatus"] == "active"
    assert paid["source"] == "purchase"
    assert paid["completedAt"] is None
    granted = rows[second_enrollment.id]
    assert granted["paymentStatus"] == "none"
    assert granted["orderId"] is None and granted["amount"] is None
    assert granted["batchId"] is None and granted["batchName"] is None
    assert granted["source"] == "admin_grant"


def test_list_filters(admin_client, enrollment, second_enrollment, course, recorded_course, other_student):
    def ids(**params):
        res = admin_client.get(URL, params)
        assert res.status_code == 200
        return {r["enrollmentId"] for r in res.json()["items"]}

    assert ids(courseId=course.id) == {enrollment.id}
    assert ids(courseId=recorded_course.id) == {second_enrollment.id}
    assert ids(paymentStatus="paid") == {enrollment.id}
    assert ids(paymentStatus="none") == {second_enrollment.id}
    assert ids(paymentStatus="all") == {enrollment.id, second_enrollment.id}
    assert ids(accessStatus="suspended") == {second_enrollment.id}
    assert ids(accessStatus="active") == {enrollment.id}
    assert ids(search="ananya") == {second_enrollment.id}
    assert ids(search="KBA-ORD-1001") == {enrollment.id}
    assert ids(search=enrollment.id[-8:]) == {enrollment.id}
    assert ids(search="nobody") == set()


def test_list_pagination(admin_client, enrollment, second_enrollment):
    res = admin_client.get(URL, {"page": 2, "pageSize": 1})
    body = res.json()
    assert body["total"] == 2
    assert body["page"] == 2
    assert body["pageSize"] == 1
    assert len(body["items"]) == 1
    # Newest first: page 1 holds the most recently enrolled row.
    first = admin_client.get(URL, {"page": 1, "pageSize": 1}).json()["items"][0]
    assert first["enrollmentId"] == second_enrollment.id


def test_list_permissions(student_client, api):
    assert student_client.get(URL).status_code == 403
    assert api.get(URL).status_code == 401


def test_grant_creates_enrollment_with_audit(admin_client, admin_user, student, recorded_course):
    res = admin_client.post(
        f"{URL}/grant",
        {"studentId": student.id, "courseId": recorded_course.id, "reason": "Scholarship awarded"},
        format="json",
    )
    assert res.status_code == 201, res.content
    row = res.json()
    assert row["studentId"] == student.id
    assert row["courseId"] == recorded_course.id
    assert row["source"] == "admin_grant"
    assert row["accessStatus"] == "active"
    assert row["paymentStatus"] == "none"
    enrollment = CourseEnrollment.objects.get(pk=row["enrollmentId"])
    assert enrollment.payment is None
    log = AuditLog.objects.get(action="enrollment.grant")
    assert log.actor == admin_user
    assert log.entity_type == "enrollment" and log.entity_id == enrollment.id
    assert "Scholarship awarded" in log.details


def test_grant_conflicts_and_validation(admin_client, enrollment, student, course, recorded_course, admin_user):
    res = admin_client.post(
        f"{URL}/grant", {"studentId": student.id, "courseId": course.id, "reason": "Duplicate grant"}, format="json"
    )
    assert res.status_code == 409
    assert res.json()["code"] == "ALREADY_ENROLLED"
    res = admin_client.post(
        f"{URL}/grant", {"studentId": student.id, "courseId": recorded_course.id, "reason": "abc"}, format="json"
    )
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
    assert "reason" in res.json()["details"]
    assert (
        admin_client.post(
            f"{URL}/grant",
            {"studentId": "usr_nope", "courseId": recorded_course.id, "reason": "Valid reason"},
            format="json",
        ).status_code
        == 404
    )
    assert (
        admin_client.post(
            f"{URL}/grant", {"studentId": student.id, "courseId": "crs_nope", "reason": "Valid reason"}, format="json"
        ).status_code
        == 404
    )
    # Admin accounts are not students.
    assert (
        admin_client.post(
            f"{URL}/grant",
            {"studentId": admin_user.id, "courseId": recorded_course.id, "reason": "Valid reason"},
            format="json",
        ).status_code
        == 404
    )
    assert AuditLog.objects.count() == 0


def test_grant_reactivates_revoked_row(admin_client, enrollment, student, course):
    enrollment.access_status = "revoked"
    enrollment.save()
    res = admin_client.post(
        f"{URL}/grant", {"studentId": student.id, "courseId": course.id, "reason": "Goodwill re-grant"}, format="json"
    )
    assert res.status_code == 201
    assert CourseEnrollment.objects.filter(student=student, course=course).count() == 1
    enrollment.refresh_from_db()
    assert enrollment.access_status == "active"
    assert enrollment.source == "admin_grant"


def test_grant_permissions(student_client, api, student, recorded_course):
    payload = {"studentId": student.id, "courseId": recorded_course.id, "reason": "Valid reason"}
    assert student_client.post(f"{URL}/grant", payload, format="json").status_code == 403
    assert api.post(f"{URL}/grant", payload, format="json").status_code == 401


def test_access_suspend_and_reactivate(admin_client, admin_user, enrollment):
    res = admin_client.post(
        f"{URL}/{enrollment.id}/access", {"accessStatus": "suspended", "reason": "Payment dispute"}, format="json"
    )
    assert res.status_code == 200, res.content
    assert res.json()["accessStatus"] == "suspended"
    enrollment.refresh_from_db()
    assert enrollment.access_status == "suspended"
    log = AuditLog.objects.get(action="enrollment.suspended")
    assert log.entity_id == enrollment.id and log.actor == admin_user
    assert "active -> suspended" in log.details
    res = admin_client.post(
        f"{URL}/{enrollment.id}/access", {"accessStatus": "active", "reason": "Dispute resolved"}, format="json"
    )
    assert res.status_code == 200
    assert res.json()["accessStatus"] == "active"
    assert AuditLog.objects.filter(action="enrollment.active").exists()


@pytest.mark.parametrize("current", ["revoked", "pending_payment"])
def test_access_invalid_transition(admin_client, enrollment, current):
    enrollment.access_status = current
    enrollment.save()
    res = admin_client.post(
        f"{URL}/{enrollment.id}/access", {"accessStatus": "active", "reason": "Trying anyway"}, format="json"
    )
    assert res.status_code == 409
    assert res.json()["code"] == "INVALID_TRANSITION"
    assert AuditLog.objects.count() == 0


def test_access_validation_and_permissions(admin_client, student_client, api, enrollment):
    res = admin_client.post(
        f"{URL}/{enrollment.id}/access", {"accessStatus": "revoked", "reason": "Not allowed here"}, format="json"
    )
    assert res.status_code == 400
    assert "accessStatus" in res.json()["details"]
    assert (
        admin_client.post(
            f"{URL}/{enrollment.id}/access", {"accessStatus": "suspended", "reason": "no"}, format="json"
        ).status_code
        == 400
    )
    assert (
        admin_client.post(
            f"{URL}/enr_nope/access", {"accessStatus": "suspended", "reason": "Valid reason"}, format="json"
        ).status_code
        == 404
    )
    assert (
        student_client.post(
            f"{URL}/{enrollment.id}/access", {"accessStatus": "suspended", "reason": "Valid reason"}, format="json"
        ).status_code
        == 403
    )
    assert (
        api.post(
            f"{URL}/{enrollment.id}/access", {"accessStatus": "suspended", "reason": "Valid reason"}, format="json"
        ).status_code
        == 401
    )
