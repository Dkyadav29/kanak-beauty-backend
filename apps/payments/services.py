"""
Payment services: order creation, signed webhook processing, reconciliation and refunds.

Flow (docs/API_CONTRACTS.md "Payments"):
  1. create_order() creates a PENDING PaymentTransaction (pricing from Refer & Earn).
  2. The gateway posts a signed webhook; handle_webhook() verifies the HMAC over the raw
     body, records the event once (WebhookEvent unique per provider+event id) and settles
     the payment under a row lock: paid -> grant access + referral rewards; failed -> reason.
  3. Nothing else can set a payment to paid. Reconciliation only applies what the gateway
     adapter reports (the sandbox adapter reports nothing).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from djangorestframework_camel_case.util import underscoreize

from apps.core.services import log_audit
from apps.courses.models import Course
from apps.enrollments import services as enrollment_services
from apps.payments.gateway import get_gateway
from apps.payments.models import PaymentTransaction, WebhookEvent
from apps.referrals import services as referral_services
from common.exceptions import ApiError, ConflictError, NotFoundError, ValidationFailed

SIGNATURE_HEADER = "X-Kanak-Signature"
ORDER_SEQUENCE_OFFSET = 1100


class InvalidSignature(ApiError):
    status_code = 401
    default_code = "INVALID_SIGNATURE"
    default_detail = "Webhook signature verification failed."


@dataclass
class WebhookResult:
    payment: PaymentTransaction | None
    idempotent: bool


# --- Orders ------------------------------------------------------------------------------------


def _order_id_candidates():
    base = ORDER_SEQUENCE_OFFSET + PaymentTransaction.objects.count()
    for offset in range(5):
        yield f"{settings.ORDER_ID_PREFIX}-{base + offset}"
    while True:
        yield f"{settings.ORDER_ID_PREFIX}-{base}-{secrets.token_hex(2).upper()}"


def create_order(student, *, course_id: str, apply_wallet_credit: bool = False) -> PaymentTransaction:
    course = Course.objects.filter(Q(pk=course_id) | Q(slug=course_id), status="published").first()
    if course is None:
        raise NotFoundError("Course not found.")
    if enrollment_services.is_entitled(student, course.id):
        raise ConflictError("You already have access to this course.", code="ALREADY_ENROLLED")
    pricing = referral_services.price_order(student, course.effective_price, apply_wallet_credit)

    # Idempotency: reuse a recent pending order for the same course and the same pricing.
    cutoff = timezone.now() - timedelta(minutes=settings.PAYMENT_PENDING_REUSE_MINUTES)
    existing = (
        PaymentTransaction.objects.filter(
            student=student,
            course=course,
            status="pending",
            amount=pricing.amount,
            wallet_credit_applied=pricing.wallet_credit_applied,
            referral_discount=pricing.referral_discount,
            created_at__gte=cutoff,
        )
        .order_by("-created_at")
        .first()
    )
    if existing is not None:
        return existing

    for attempt, order_id in enumerate(_order_id_candidates()):
        try:
            with transaction.atomic():
                return PaymentTransaction.objects.create(
                    order_id=order_id,
                    student=student,
                    course=course,
                    amount=pricing.amount,
                    currency=course.currency,
                    list_price=pricing.list_price,
                    referral_discount=pricing.referral_discount,
                    wallet_credit_applied=pricing.wallet_credit_applied,
                    status="pending",
                    gateway_provider=settings.PAYMENT_GATEWAY,
                    checkout_token=secrets.token_urlsafe(24),
                )
        except IntegrityError:
            if attempt >= 8:
                raise
    raise RuntimeError("unreachable")  # pragma: no cover


# --- Webhook -----------------------------------------------------------------------------------


def sign_payload(raw_body: bytes, secret: str | None = None) -> str:
    key = (secret or settings.PAYMENT_WEBHOOK_SECRET).encode()
    return hmac.new(key, raw_body, hashlib.sha256).hexdigest()


def verify_signature(raw_body: bytes, signature: str | None) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(sign_payload(raw_body), signature.strip())


def parse_webhook_body(raw_body: bytes) -> dict:
    from apps.payments.serializers import WebhookPayloadSerializer

    try:
        data = json.loads(raw_body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, ValueError):
        raise ValidationFailed("Webhook body must be valid JSON.")
    if not isinstance(data, dict):
        raise ValidationFailed("Webhook body must be a JSON object.")
    serializer = WebhookPayloadSerializer(data=underscoreize(data))
    serializer.is_valid(raise_exception=True)
    return dict(serializer.validated_data)


def _settle(payment: PaymentTransaction, *, status: str, gateway_reference: str, method: str, failure_reason: str = ""):
    """Apply a gateway outcome to a PENDING payment. Caller holds the row lock inside a transaction."""
    now = timezone.now()
    if gateway_reference:
        payment.gateway_reference = gateway_reference
    if method:
        payment.method = method
    if status == "failed":
        payment.status = "failed"
        payment.failure_reason = failure_reason or "Payment was not completed."
        payment.save()
        return
    payment.status = "paid"
    payment.paid_at = now
    payment.invoice_available = True
    payment.failure_reason = ""
    payment.save()
    # Grant access (exactly one enrollment per student+course) and apply Refer & Earn in the same transaction.
    enrollment_services.grant_access_for_payment(payment)
    referral_services.on_payment_paid(payment)


def process_webhook(payload: dict, *, provider: str | None = None) -> WebhookResult:
    provider = provider or settings.PAYMENT_GATEWAY
    with transaction.atomic():
        event, created = WebhookEvent.objects.get_or_create(
            provider=provider,
            event_id=payload["event_id"],
            defaults={"event_type": f"payment.{payload['status']}", "payload": payload},
        )
        if not created:
            return WebhookResult(payment=None, idempotent=True)
        payment = PaymentTransaction.objects.select_for_update().filter(pk=payload["payment_id"]).first()
        if payment is None:
            raise NotFoundError("Payment not found.")
        event.payment = payment
        if payment.status != "pending":
            event.result = "already_settled"
            event.processed_at = timezone.now()
            event.save(update_fields=["payment", "result", "processed_at"])
            return WebhookResult(payment=payment, idempotent=True)
        _settle(
            payment,
            status=payload["status"],
            gateway_reference=payload.get("gateway_reference", ""),
            method=payload.get("method", ""),
            failure_reason=payload.get("failure_reason", ""),
        )
        event.result = payment.status
        event.processed_at = timezone.now()
        event.save(update_fields=["payment", "result", "processed_at"])
        return WebhookResult(payment=payment, idempotent=False)


def handle_webhook(raw_body: bytes, signature: str | None) -> WebhookResult:
    """Verify the HMAC over the raw body, then process idempotently."""
    if not verify_signature(raw_body, signature):
        raise InvalidSignature()
    return process_webhook(parse_webhook_body(raw_body))


def sandbox_complete(student, *, checkout_token: str, outcome: str, method: str = "UPI") -> PaymentTransaction:
    """Sandbox stand-in for the hosted checkout: builds and signs a webhook and runs the real processor."""
    payment = PaymentTransaction.objects.filter(checkout_token=checkout_token, student=student).first()
    if payment is None:
        raise NotFoundError("Order not found.")
    status = "paid" if outcome == "success" else "failed"
    body = {
        "eventId": f"sandbox_{secrets.token_hex(8)}",
        "paymentId": payment.id,
        "status": status,
        "gatewayReference": f"gw_{secrets.token_hex(5)}",
        "method": method or "UPI",
    }
    if status == "failed":
        body["failureReason"] = "Payment declined by the gateway (simulated)."
    raw = json.dumps(body).encode()
    handle_webhook(raw, sign_payload(raw))
    payment.refresh_from_db()
    return payment


# --- Admin -------------------------------------------------------------------------------------


def admin_payments_queryset():
    return PaymentTransaction.objects.select_related("student", "course").order_by("-created_at")


def _parse_from(value: str):
    from django.utils.dateparse import parse_date, parse_datetime

    dt = parse_datetime(value)
    if dt is not None:
        return Q(created_at__gte=dt if timezone.is_aware(dt) else timezone.make_aware(dt))
    day = parse_date(value)
    if day is not None:
        return Q(created_at__date__gte=day)
    raise ValidationFailed("from must be an ISO date or datetime.", details={"from": ["Invalid date"]})


def _parse_to(value: str):
    from django.utils.dateparse import parse_date, parse_datetime

    dt = parse_datetime(value)
    day = dt.date() if dt is not None else parse_date(value)
    if day is None:
        raise ValidationFailed("to must be an ISO date or datetime.", details={"to": ["Invalid date"]})
    return Q(created_at__date__lte=day)


def filter_admin_payments(queryset, *, search=None, status=None, date_from=None, date_to=None):
    if status:
        queryset = queryset.filter(status=status)
    if date_from:
        queryset = queryset.filter(_parse_from(date_from))
    if date_to:
        queryset = queryset.filter(_parse_to(date_to))
    if search:
        queryset = queryset.filter(
            Q(order_id__icontains=search)
            | Q(student__name__icontains=search)
            | Q(course__title__icontains=search)
            | Q(gateway_reference__icontains=search)
        )
    return queryset


def _locked_payment(payment_id: str) -> PaymentTransaction:
    payment = (
        PaymentTransaction.objects.select_for_update().select_related("student", "course").filter(pk=payment_id).first()
    )
    if payment is None:
        raise NotFoundError("Payment not found.")
    return payment


def reconcile_payment(*, payment_id: str, actor, request=None) -> tuple[PaymentTransaction, str, bool]:
    """
    Re-query the gateway for the authoritative status. A pending payment transitions to
    paid/failed ONLY when the provider adapter reports that outcome. Admins cannot force "paid".
    """
    with transaction.atomic():
        payment = _locked_payment(payment_id)
        reported = get_gateway().fetch_status(payment)
        changed = False
        if reported is not None and payment.status == "pending" and reported.status in ("paid", "failed"):
            _settle(payment, status=reported.status, gateway_reference=reported.reference, method=reported.method)
            changed = True
        gateway_status = reported.status if reported is not None else payment.status
        log_audit(
            actor=actor,
            action="payment.reconcile",
            entity_type="payment",
            entity_id=payment.id,
            details=f'Reconciliation requested for {payment.order_id}; gateway reports "{gateway_status}".',
            request=request,
        )
    return payment, gateway_status, changed


def refund_payment(*, payment_id: str, reason: str, actor, request=None) -> PaymentTransaction:
    with transaction.atomic():
        payment = _locked_payment(payment_id)
        if payment.status != "paid":
            raise ConflictError("Only paid transactions can be refunded.", code="NOT_REFUNDABLE")
        payment.status = "refunded"
        payment.refund_status = "refunded"
        payment.refunded_at = timezone.now()
        payment.refund_reason = reason
        payment.save()
        enrollment_services.revoke_access_for_refund(payment, actor=actor)
        referral_services.on_payment_refunded(payment)
        log_audit(
            actor=actor,
            action="payment.refund",
            entity_type="payment",
            entity_id=payment.id,
            details=f"Refunded {payment.order_id} ({payment.currency} {payment.amount}). Reason: {reason}",
            request=request,
        )
    return payment
