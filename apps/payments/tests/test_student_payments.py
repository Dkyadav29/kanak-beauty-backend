"""GET /student/payments and /student/payments/:id (ownership -> 404)."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

URL = "/api/v1/student/payments"


def test_list_own_payments_shape(
    student_client, student, enrollment, course, pending_payment, make_payment, other_student, recorded_course
):
    make_payment(other_student, recorded_course)
    res = student_client.get(URL)
    assert res.status_code == 200
    rows = res.json()
    assert isinstance(rows, list)
    assert [r["id"] for r in rows] == [pending_payment.id, enrollment.payment_id]
    paid = rows[1]
    assert set(paid) == {
        "id",
        "orderId",
        "studentId",
        "studentName",
        "courseId",
        "courseTitle",
        "amount",
        "currency",
        "listPrice",
        "referralDiscount",
        "walletCreditApplied",
        "status",
        "refundStatus",
        "method",
        "gatewayReference",
        "invoiceAvailable",
        "createdAt",
        "paidAt",
        "failureReason",
    }
    assert paid["studentName"] == student.name
    assert paid["courseTitle"] == course.title
    assert paid["amount"] == 29999.0
    assert paid["listPrice"] == 29999.0
    assert paid["referralDiscount"] == 0
    assert paid["walletCreditApplied"] == 0
    assert paid["status"] == "paid"
    assert paid["refundStatus"] == "none"
    assert paid["method"] == "UPI"
    assert paid["gatewayReference"] == "gw_test_1001"
    assert paid["invoiceAvailable"] is True
    assert paid["paidAt"] is not None
    assert paid["failureReason"] is None
    pending = rows[0]
    assert pending["method"] is None
    assert pending["gatewayReference"] is None
    assert pending["paidAt"] is None
    assert "checkoutToken" not in pending


def test_detail_and_ownership(student_client, other_student_client, pending_payment):
    res = student_client.get(f"{URL}/{pending_payment.id}")
    assert res.status_code == 200
    assert res.json()["id"] == pending_payment.id
    res = other_student_client.get(f"{URL}/{pending_payment.id}")
    assert res.status_code == 404
    assert res.json()["code"] == "NOT_FOUND"
    assert student_client.get(f"{URL}/pay_nope").status_code == 404


def test_permissions(admin_client, api, pending_payment):
    assert admin_client.get(URL).status_code == 403
    assert admin_client.get(f"{URL}/{pending_payment.id}").status_code == 403
    assert api.get(URL).status_code == 401
    assert api.get(f"{URL}/{pending_payment.id}").status_code == 401
