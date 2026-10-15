"""Webhook signature, idempotency and settlement; sandbox checkout simulator."""

from __future__ import annotations

import importlib
import json

import pytest
from django.test import override_settings

from apps.enrollments.models import CourseEnrollment
from apps.payments.models import PaymentTransaction, WebhookEvent
from apps.payments.tests.conftest import WEBHOOK_URL, paid_event, post_webhook, sign

pytestmark = pytest.mark.django_db


def test_rejects_bad_signature(api, pending_payment):
    res = post_webhook(api, paid_event(pending_payment), signature="deadbeef")
    assert res.status_code == 401
    assert res.json()["code"] == "INVALID_SIGNATURE"
    res = post_webhook(api, paid_event(pending_payment), secret="wrong-secret")
    assert res.status_code == 401
    raw = json.dumps(paid_event(pending_payment)).encode()
    assert api.post(WEBHOOK_URL, data=raw, content_type="application/json").status_code == 401
    pending_payment.refresh_from_db()
    assert pending_payment.status == "pending"
    assert WebhookEvent.objects.count() == 0
    assert CourseEnrollment.objects.count() == 0


def test_signature_covers_raw_body(api, pending_payment):
    body = paid_event(pending_payment)
    raw = json.dumps(body).encode()
    tampered = json.dumps({**body, "status": "failed"}).encode()
    res = api.post(WEBHOOK_URL, data=tampered, content_type="application/json", HTTP_X_KANAK_SIGNATURE=sign(raw))
    assert res.status_code == 401


def test_paid_grants_exactly_one_enrollment(api, pending_payment, student, recorded_course):
    res = post_webhook(api, paid_event(pending_payment, event_id="evt_paid_1"))
    assert res.status_code == 200, res.content
    assert res.json() == {"received": True, "idempotent": False}
    pending_payment.refresh_from_db()
    assert pending_payment.status == "paid"
    assert pending_payment.paid_at is not None
    assert pending_payment.invoice_available is True
    assert pending_payment.gateway_reference == "gw_abc123"
    assert pending_payment.method == "UPI"
    enrollments = CourseEnrollment.objects.filter(student=student, course=recorded_course)
    assert enrollments.count() == 1
    enrollment = enrollments.get()
    assert enrollment.access_status == "active"
    assert enrollment.source == "purchase"
    assert enrollment.payment == pending_payment
    event = WebhookEvent.objects.get(provider="sandbox", event_id="evt_paid_1")
    assert event.payment == pending_payment
    assert event.result == "paid"
    assert event.processed_at is not None


def test_failed_grants_nothing(api, pending_payment):
    body = paid_event(pending_payment, status="failed", failureReason="Card declined")
    res = post_webhook(api, body)
    assert res.status_code == 200
    assert res.json()["idempotent"] is False
    pending_payment.refresh_from_db()
    assert pending_payment.status == "failed"
    assert pending_payment.failure_reason == "Card declined"
    assert pending_payment.paid_at is None
    assert pending_payment.invoice_available is False
    assert CourseEnrollment.objects.count() == 0


def test_failed_without_reason_uses_default(api, pending_payment):
    post_webhook(api, paid_event(pending_payment, status="failed"))
    pending_payment.refresh_from_db()
    assert pending_payment.failure_reason == "Payment was not completed."


def test_duplicate_event_id_is_idempotent(api, pending_payment):
    body = paid_event(pending_payment, event_id="evt_dup")
    assert post_webhook(api, body).json()["idempotent"] is False
    res = post_webhook(api, body)
    assert res.status_code == 200
    assert res.json() == {"received": True, "idempotent": True}
    assert WebhookEvent.objects.filter(event_id="evt_dup").count() == 1
    assert CourseEnrollment.objects.count() == 1


def test_replay_after_settlement_is_idempotent(api, pending_payment):
    post_webhook(api, paid_event(pending_payment, event_id="evt_1"))
    pending_payment.refresh_from_db()
    paid_at = pending_payment.paid_at
    res = post_webhook(
        api, paid_event(pending_payment, event_id="evt_2", status="failed", failureReason="late failure")
    )
    assert res.json() == {"received": True, "idempotent": True}
    pending_payment.refresh_from_db()
    assert pending_payment.status == "paid"
    assert pending_payment.paid_at == paid_at
    assert pending_payment.failure_reason == ""
    assert CourseEnrollment.objects.count() == 1
    assert WebhookEvent.objects.get(event_id="evt_2").result == "already_settled"


def test_unknown_payment_and_invalid_payload(api, pending_payment):
    body = paid_event(pending_payment, event_id="evt_x")
    body["paymentId"] = "pay_nope"
    res = post_webhook(api, body)
    assert res.status_code == 404
    # The event is not recorded, so a corrected redelivery with the same id can still be processed.
    assert WebhookEvent.objects.count() == 0
    res = post_webhook(api, {"eventId": "evt_bad", "paymentId": pending_payment.id, "status": "unknown"})
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
    raw = b"not json"
    assert (
        api.post(WEBHOOK_URL, data=raw, content_type="application/json", HTTP_X_KANAK_SIGNATURE=sign(raw)).status_code
        == 400
    )


def test_webhook_needs_no_bearer_token(api, pending_payment, student_client):
    # Authentication headers are ignored; only the signature matters.
    res = post_webhook(student_client, paid_event(pending_payment))
    assert res.status_code == 200


# --- Sandbox checkout simulator ------------------------------------------------------------------


def test_sandbox_complete_success(student_client, student, pending_payment, recorded_course):
    res = student_client.post(
        "/api/v1/payments/sandbox/complete", {"checkoutToken": pending_payment.checkout_token, "outcome": "success", "method": "Card"}, format="json"
    )
    assert res.status_code == 200, res.content
    assert res.json() == {"status": "paid"}
    pending_payment.refresh_from_db()
    assert pending_payment.status == "paid"
    assert pending_payment.method == "Card"
    assert pending_payment.gateway_reference.startswith("gw_")
    assert CourseEnrollment.objects.filter(student=student, course=recorded_course, access_status="active").count() == 1
    assert WebhookEvent.objects.filter(provider="sandbox", event_id__startswith="sandbox_").count() == 1
    # The checkout page polls this until status != pending.
    poll = student_client.get(f"/api/v1/student/payments/{pending_payment.id}")
    assert poll.json()["status"] == "paid"


def test_sandbox_complete_failure(student_client, pending_payment):
    res = student_client.post(
        "/api/v1/payments/sandbox/complete",
        {"checkoutToken": pending_payment.checkout_token, "outcome": "failure"},
        format="json",
    )
    assert res.status_code == 200
    assert res.json() == {"status": "failed"}
    pending_payment.refresh_from_db()
    assert pending_payment.status == "failed"
    assert "simulated" in pending_payment.failure_reason
    assert CourseEnrollment.objects.count() == 0


def test_sandbox_complete_errors(student_client, other_student_client, admin_client, api, pending_payment):
    url = "/api/v1/payments/sandbox/complete"
    good = {"checkoutToken": pending_payment.checkout_token, "outcome": "success"}
    assert other_student_client.post(url, good, format="json").status_code == 404
    assert (
        student_client.post(url, {"checkoutToken": "chk_nope", "outcome": "success"}, format="json").status_code == 404
    )
    res = student_client.post(url, {"checkoutToken": pending_payment.checkout_token, "outcome": "maybe"}, format="json")
    assert res.status_code == 400
    assert admin_client.post(url, good, format="json").status_code == 403
    assert api.post(url, good, format="json").status_code == 401
    pending_payment.refresh_from_db()
    assert pending_payment.status == "pending"


def test_sandbox_routes_only_registered_for_sandbox_gateway():
    from apps.payments import urls as payment_urls

    names = lambda: {p.name for p in payment_urls.urlpatterns}  # noqa: E731
    assert "payments-sandbox-complete" in names()
    try:
        with override_settings(PAYMENT_GATEWAY="razorpay"):
            importlib.reload(payment_urls)
            assert "payments-sandbox-complete" not in names()
            assert "payments-webhook" in names()
    finally:
        importlib.reload(payment_urls)
    assert "payments-sandbox-complete" in names()


def test_no_endpoint_marks_payment_paid_directly(admin_client, pending_payment):
    """There is no admin route that sets `paid`; reconcile with the sandbox provider never changes state."""
    res = admin_client.post(f"/api/v1/admin/payments/{pending_payment.id}/reconcile")
    assert res.status_code == 200
    assert res.json()["changed"] is False
    assert PaymentTransaction.objects.get(pk=pending_payment.id).status == "pending"
