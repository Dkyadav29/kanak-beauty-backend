"""POST /payments/orders: creation, conflicts, pending reuse and Refer & Earn pricing."""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.payments.models import PaymentTransaction
from apps.referrals.models import ReferralReward

pytestmark = pytest.mark.django_db

URL = "/api/v1/payments/orders"


def test_create_order_returns_pending_payment(student_client, student, recorded_course, settings):
    res = student_client.post(URL, {"courseId": recorded_course.id}, format="json")
    assert res.status_code == 201, res.content
    body = res.json()
    payment = PaymentTransaction.objects.get(pk=body["paymentId"])
    assert body["orderId"] == payment.order_id
    assert body["orderId"].startswith(f"{settings.ORDER_ID_PREFIX}-")
    assert body["amount"] == 9999.0
    assert body["listPrice"] == 9999.0
    assert body["referralDiscount"] == 0
    assert body["walletCreditApplied"] == 0
    assert body["currency"] == "INR"
    assert body["gateway"] == {"provider": "sandbox", "checkoutToken": payment.checkout_token}
    assert len(payment.checkout_token) >= 24
    assert payment.status == "pending"
    assert payment.student == student
    assert payment.gateway_provider == "sandbox"
    assert "secret" not in str(body).lower()


def test_create_order_by_slug(student_client, recorded_course):
    assert student_client.post(URL, {"courseId": recorded_course.slug}, format="json").status_code == 201


def test_create_order_unknown_or_unpublished_course(student_client, draft_course):
    res = student_client.post(URL, {"courseId": draft_course.id}, format="json")
    assert res.status_code == 404
    assert res.json()["code"] == "NOT_FOUND"
    assert student_client.post(URL, {"courseId": "crs_nope"}, format="json").status_code == 404
    res = student_client.post(URL, {}, format="json")
    assert res.status_code == 400
    assert "courseId" in res.json()["details"]


def test_create_order_conflicts_when_already_enrolled(student_client, enrollment, course):
    res = student_client.post(URL, {"courseId": course.id}, format="json")
    assert res.status_code == 409
    assert res.json()["code"] == "ALREADY_ENROLLED"


def test_create_order_allowed_after_revocation(student_client, enrollment, course):
    enrollment.access_status = "revoked"
    enrollment.save()
    assert student_client.post(URL, {"courseId": course.id}, format="json").status_code == 201


def test_pending_order_is_reused_within_window(student_client, recorded_course, settings):
    first = student_client.post(URL, {"courseId": recorded_course.id}, format="json").json()
    second = student_client.post(URL, {"courseId": recorded_course.id}, format="json").json()
    assert first["paymentId"] == second["paymentId"]
    assert first["orderId"] == second["orderId"]
    assert PaymentTransaction.objects.count() == 1
    # Once the reuse window has passed a new order is created.
    PaymentTransaction.objects.update(
        created_at=timezone.now() - timedelta(minutes=settings.PAYMENT_PENDING_REUSE_MINUTES + 1)
    )
    third = student_client.post(URL, {"courseId": recorded_course.id}, format="json").json()
    assert third["paymentId"] != first["paymentId"]
    assert third["orderId"] != first["orderId"]
    assert PaymentTransaction.objects.count() == 2


def test_pending_order_not_reused_when_pricing_differs(student_client, student, recorded_course, referral_config):
    first = student_client.post(URL, {"courseId": recorded_course.id}, format="json").json()
    ReferralReward.objects.create(user=student, amount=Decimal("700"), status="credited", note="Seed credit")
    second = student_client.post(URL, {"courseId": recorded_course.id, "applyWalletCredit": True}, format="json").json()
    assert second["paymentId"] != first["paymentId"]
    assert second["walletCreditApplied"] == 700.0
    assert second["amount"] == 9999.0 - 700


def test_order_ids_are_unique(student_client, other_student_client, recorded_course, course):
    a = student_client.post(URL, {"courseId": recorded_course.id}, format="json").json()
    b = other_student_client.post(URL, {"courseId": recorded_course.id}, format="json").json()
    c = student_client.post(URL, {"courseId": course.id}, format="json").json()
    assert len({a["orderId"], b["orderId"], c["orderId"]}) == 3


def test_referral_discount_on_first_purchase(student_client, student, other_student, recorded_course, referral_config):
    student.referred_by = other_student
    student.save()
    body = student_client.post(URL, {"courseId": recorded_course.id}, format="json").json()
    assert body["listPrice"] == 9999.0
    assert body["referralDiscount"] == 300.0
    assert body["amount"] == 9699.0
    payment = PaymentTransaction.objects.get(pk=body["paymentId"])
    assert payment.referral_discount == Decimal("300.00")
    assert payment.amount == Decimal("9699.00")


def test_no_referral_discount_after_previous_purchase(
    student_client, student, other_student, recorded_course, enrollment, referral_config
):
    student.referred_by = other_student
    student.save()
    body = student_client.post(URL, {"courseId": recorded_course.id}, format="json").json()
    assert body["referralDiscount"] == 0
    assert body["amount"] == 9999.0


def test_wallet_credit_capped_and_optional(student_client, student, recorded_course, referral_config):
    ReferralReward.objects.create(user=student, amount=Decimal("2500"), status="credited", note="Seed credit")
    without = student_client.post(URL, {"courseId": recorded_course.id}, format="json").json()
    assert without["walletCreditApplied"] == 0
    with_credit = student_client.post(
        URL, {"courseId": recorded_course.id, "applyWalletCredit": True}, format="json"
    ).json()
    assert with_credit["walletCreditApplied"] == 2000.0  # max_credit_per_order default
    assert with_credit["amount"] == 7999.0
    referral_config.max_credit_per_order = 500
    referral_config.save()
    PaymentTransaction.objects.all().delete()
    capped = student_client.post(URL, {"courseId": recorded_course.id, "applyWalletCredit": True}, format="json").json()
    assert capped["walletCreditApplied"] == 500.0


def test_discount_and_wallet_combined(student_client, student, other_student, recorded_course, referral_config):
    student.referred_by = other_student
    student.save()
    ReferralReward.objects.create(user=student, amount=Decimal("150"), status="credited", note="Seed credit")
    body = student_client.post(URL, {"courseId": recorded_course.id, "applyWalletCredit": True}, format="json").json()
    assert body["referralDiscount"] == 300.0
    assert body["walletCreditApplied"] == 150.0
    assert body["amount"] == 9999.0 - 300 - 150


def test_create_order_permissions(admin_client, api, recorded_course):
    assert admin_client.post(URL, {"courseId": recorded_course.id}, format="json").status_code == 403
    assert api.post(URL, {"courseId": recorded_course.id}, format="json").status_code == 401
