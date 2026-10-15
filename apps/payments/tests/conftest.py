"""Fixtures and helpers shared by the payments tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import uuid

import pytest
from django.conf import settings

WEBHOOK_URL = "/api/v1/payments/webhook"


def sign(raw: bytes, secret: str | None = None) -> str:
    return hmac.new((secret or settings.PAYMENT_WEBHOOK_SECRET).encode(), raw, hashlib.sha256).hexdigest()


def post_webhook(client, body: dict, *, signature: str | None = None, secret: str | None = None):
    raw = json.dumps(body).encode()
    sig = signature if signature is not None else sign(raw, secret)
    return client.post(WEBHOOK_URL, data=raw, content_type="application/json", HTTP_X_KANAK_SIGNATURE=sig)


def paid_event(payment, event_id: str | None = None, **extra) -> dict:
    body = {
        "eventId": event_id or f"evt_{uuid.uuid4().hex[:12]}",
        "paymentId": payment.id,
        "status": "paid",
        "gatewayReference": "gw_abc123",
        "method": "UPI",
    }
    body.update(extra)
    return body


@pytest.fixture
def make_payment(db):
    from apps.payments.models import PaymentTransaction

    def _make(student, course, status="pending", **overrides):
        data = {
            "order_id": f"KBA-ORD-{uuid.uuid4().hex[:8].upper()}",
            "student": student,
            "course": course,
            "amount": course.effective_price,
            "list_price": course.effective_price,
            "status": status,
            "gateway_provider": "sandbox",
            "checkout_token": f"chk_{uuid.uuid4().hex}",
        }
        data.update(overrides)
        return PaymentTransaction.objects.create(**data)

    return _make


@pytest.fixture
def pending_payment(make_payment, student, recorded_course):
    return make_payment(student, recorded_course)
