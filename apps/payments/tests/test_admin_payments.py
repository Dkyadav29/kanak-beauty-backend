"""Admin payments: list filters/pagination, reconcile (never forces paid) and refunds."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.batches.models import BatchEnrollment, BatchHistoryEvent
from apps.core.models import AuditLog
from apps.payments.gateway import GatewayStatus, SandboxGateway
from apps.payments.models import PaymentTransaction
from apps.referrals.models import Referral, ReferralReward

pytestmark = pytest.mark.django_db

URL = "/api/v1/admin/payments"


def test_list_and_filters(admin_client, enrollment, pending_payment, make_payment, other_student, recorded_course):
    failed = make_payment(other_student, recorded_course, status="failed", gateway_reference="gw_fail_77")
    PaymentTransaction.objects.filter(pk=failed.pk).update(created_at=timezone.now() - timedelta(days=10))

    res = admin_client.get(URL)
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 3
    assert [r["id"] for r in body["items"]] == [pending_payment.id, enrollment.payment_id, failed.id]
    assert body["items"][1]["studentName"] == enrollment.student.name

    def ids(**params):
        return {r["id"] for r in admin_client.get(URL, params).json()["items"]}

    assert ids(status="paid") == {enrollment.payment_id}
    assert ids(status="failed") == {failed.id}
    assert ids(status="all") == {pending_payment.id, enrollment.payment_id, failed.id}
    assert ids(search="KBA-ORD-1001") == {enrollment.payment_id}
    assert ids(search="ananya") == {failed.id}
    assert ids(search="gw_fail") == {failed.id}
    assert ids(search="salon management") == {pending_payment.id, failed.id}
    today = timezone.localdate().isoformat()
    yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
    assert ids(**{"from": yesterday}) == {pending_payment.id, enrollment.payment_id}
    assert ids(to=yesterday) == {failed.id}
    assert ids(**{"from": yesterday, "to": today}) == {pending_payment.id, enrollment.payment_id}
    assert admin_client.get(URL, {"from": "not-a-date"}).status_code == 400


def test_list_pagination(admin_client, enrollment, pending_payment):
    body = admin_client.get(URL, {"page": 2, "pageSize": 1}).json()
    assert body == {"items": [body["items"][0]], "total": 2, "page": 2, "pageSize": 1}
    assert body["items"][0]["id"] == enrollment.payment_id


def test_list_permissions(student_client, api):
    assert student_client.get(URL).status_code == 403
    assert api.get(URL).status_code == 401


def test_reconcile_never_marks_paid_with_sandbox(admin_client, admin_user, pending_payment):
    res = admin_client.post(f"{URL}/{pending_payment.id}/reconcile")
    assert res.status_code == 200, res.content
    body = res.json()
    assert body["changed"] is False
    assert body["gatewayStatus"] == "pending"
    assert body["payment"]["id"] == pending_payment.id
    assert body["payment"]["status"] == "pending"
    pending_payment.refresh_from_db()
    assert pending_payment.status == "pending"
    log = AuditLog.objects.get(action="payment.reconcile")
    assert log.actor == admin_user and log.entity_type == "payment" and log.entity_id == pending_payment.id
    assert pending_payment.order_id in log.details


def test_reconcile_applies_only_the_gateway_answer(
    admin_client, pending_payment, monkeypatch, student, recorded_course
):
    from apps.enrollments.models import CourseEnrollment

    monkeypatch.setattr(
        SandboxGateway, "fetch_status", lambda self, payment: GatewayStatus("paid", "gw_recon_1", "Card")
    )
    body = admin_client.post(f"{URL}/{pending_payment.id}/reconcile").json()
    assert body["changed"] is True
    assert body["gatewayStatus"] == "paid"
    assert body["payment"]["status"] == "paid"
    assert body["payment"]["gatewayReference"] == "gw_recon_1"
    assert CourseEnrollment.objects.filter(student=student, course=recorded_course, access_status="active").count() == 1
    # Already settled payments are never touched again.
    body = admin_client.post(f"{URL}/{pending_payment.id}/reconcile").json()
    assert body["changed"] is False


def test_reconcile_errors(admin_client, student_client, api, pending_payment):
    assert admin_client.post(f"{URL}/pay_nope/reconcile").status_code == 404
    assert student_client.post(f"{URL}/{pending_payment.id}/reconcile").status_code == 403
    assert api.post(f"{URL}/{pending_payment.id}/reconcile").status_code == 401


def test_refund_revokes_access_and_withdraws(admin_client, admin_user, enrollment, batch, student, course):
    payment = enrollment.payment
    res = admin_client.post(
        f"{URL}/{payment.id}/refund", {"reason": "Student withdrew within cooling-off period"}, format="json"
    )
    assert res.status_code == 200, res.content
    body = res.json()
    assert body["id"] == payment.id
    assert body["status"] == "refunded"
    assert body["refundStatus"] == "refunded"
    payment.refresh_from_db()
    assert payment.refunded_at is not None
    assert payment.refund_reason.startswith("Student withdrew")
    enrollment.refresh_from_db()
    assert enrollment.access_status == "revoked"
    assert BatchEnrollment.objects.get(batch=batch, student=student).status == "withdrawn"
    history = BatchHistoryEvent.objects.get(batch=batch, type="student_withdrawn")
    assert history.actor == admin_user
    log = AuditLog.objects.get(action="payment.refund")
    assert log.actor == admin_user and log.entity_id == payment.id
    assert "cooling-off" in log.details
    # Student loses access.
    assert enrollment.student.enrollments.filter(access_status="active").count() == 0


def test_refund_reverses_referral_reward_and_restores_credit(
    admin_client, enrollment, student, other_student, referral_config
):
    payment = enrollment.payment
    payment.wallet_credit_applied = Decimal("400")
    payment.save()
    referral = Referral.objects.create(
        referrer=other_student,
        referee=student,
        referral_code=other_student.referral_code,
        status="rewarded",
        reward_amount=Decimal("500"),
        payment=payment,
    )
    ReferralReward.objects.create(
        referral=referral, user=other_student, payment=payment, amount=Decimal("500"), status="credited", note="Reward"
    )
    ReferralReward.objects.create(
        user=student,
        payment=payment,
        amount=Decimal("-400"),
        status="redeemed",
        note=f"Credit applied to order {payment.order_id}.",
    )
    res = admin_client.post(f"{URL}/{payment.id}/refund", {"reason": "Chargeback received"}, format="json")
    assert res.status_code == 200
    referral.refresh_from_db()
    assert referral.status == "cancelled"
    reversed_entry = ReferralReward.objects.get(status="reversed")
    assert reversed_entry.user == other_student and reversed_entry.amount == Decimal("-500")
    restored = ReferralReward.objects.get(user=student, status="credited")
    assert restored.amount == Decimal("400")
    from apps.referrals.services import wallet_balance

    assert wallet_balance(other_student) == 0
    assert wallet_balance(student) == 0


@pytest.mark.parametrize("status", ["pending", "failed", "refunded"])
def test_refund_only_paid(admin_client, make_payment, student, recorded_course, status):
    payment = make_payment(student, recorded_course, status=status)
    res = admin_client.post(f"{URL}/{payment.id}/refund", {"reason": "Not refundable here"}, format="json")
    assert res.status_code == 409
    assert res.json()["code"] == "NOT_REFUNDABLE"
    assert AuditLog.objects.count() == 0


def test_refund_validation_and_permissions(admin_client, student_client, api, enrollment):
    payment = enrollment.payment
    res = admin_client.post(f"{URL}/{payment.id}/refund", {"reason": "abc"}, format="json")
    assert res.status_code == 400
    assert "reason" in res.json()["details"]
    assert admin_client.post(f"{URL}/pay_nope/refund", {"reason": "Valid reason"}, format="json").status_code == 404
    assert (
        student_client.post(f"{URL}/{payment.id}/refund", {"reason": "Valid reason"}, format="json").status_code == 403
    )
    assert api.post(f"{URL}/{payment.id}/refund", {"reason": "Valid reason"}, format="json").status_code == 401
    payment.refresh_from_db()
    assert payment.status == "paid"
