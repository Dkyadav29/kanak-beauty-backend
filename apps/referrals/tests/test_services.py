"""Refer & Earn business rules: wallet, pricing, rewards on paid, reversal on refund."""

from __future__ import annotations

import uuid
from decimal import Decimal

import pytest

from apps.payments.models import PaymentTransaction
from apps.referrals import services
from apps.referrals.models import Referral, ReferralReward

pytestmark = pytest.mark.django_db


def _payment(student, course, status="pending", **overrides):
    data = {
        "order_id": f"KBA-ORD-{uuid.uuid4().hex[:8].upper()}",
        "student": student,
        "course": course,
        "amount": course.effective_price,
        "list_price": course.effective_price,
        "status": status,
        "checkout_token": f"chk_{uuid.uuid4().hex}",
    }
    data.update(overrides)
    return PaymentTransaction.objects.create(**data)


@pytest.fixture
def referred(student, other_student, referral_config):
    student.referred_by = other_student
    student.save()
    return Referral.objects.create(referrer=other_student, referee=student, referral_code=other_student.referral_code)


def test_wallet_balance_sums_ledger(student):
    assert services.wallet_balance(student) == Decimal("0.00")
    ReferralReward.objects.create(user=student, amount=Decimal("500"), status="credited", note="a")
    ReferralReward.objects.create(user=student, amount=Decimal("-200"), status="redeemed", note="b")
    ReferralReward.objects.create(user=student, amount=Decimal("-100"), status="reversed", note="c")
    assert services.wallet_balance(student) == Decimal("200.00")


def test_price_order_without_referral(student, referral_config):
    pricing = services.price_order(student, Decimal("9999"), False)
    assert (pricing.list_price, pricing.referral_discount, pricing.wallet_credit_applied, pricing.amount) == (
        Decimal("9999.00"),
        Decimal("0.00"),
        Decimal("0.00"),
        Decimal("9999.00"),
    )


def test_price_order_first_purchase_discount(student, referred, referral_config):
    pricing = services.price_order(student, Decimal("9999"), False)
    assert pricing.referral_discount == Decimal("300.00")
    assert pricing.amount == Decimal("9699.00")
    # Discount never exceeds the price.
    assert services.price_order(student, Decimal("100"), False).amount == Decimal("0.00")


def test_price_order_no_discount_after_paid_or_refunded(student, referred, recorded_course):
    _payment(student, recorded_course, status="refunded")
    assert services.price_order(student, Decimal("9999"), False).referral_discount == Decimal("0.00")


def test_price_order_discount_requires_programme_and_active_referrer(student, other_student, referred, referral_config):
    referral_config.enabled = False
    referral_config.save()
    assert services.price_order(student, Decimal("9999"), False).referral_discount == Decimal("0.00")
    referral_config.enabled = True
    referral_config.save()
    other_student.is_active = False
    other_student.save()
    assert services.price_order(student, Decimal("9999"), False).referral_discount == Decimal("0.00")


def test_price_order_wallet_credit_rules(student, referral_config):
    ReferralReward.objects.create(user=student, amount=Decimal("2500"), status="credited", note="seed")
    assert services.price_order(student, Decimal("9999"), False).wallet_credit_applied == Decimal("0.00")
    pricing = services.price_order(student, Decimal("9999"), True)
    assert pricing.wallet_credit_applied == Decimal("2000.00")  # capped by max_credit_per_order
    assert pricing.amount == Decimal("7999.00")
    assert services.price_order(student, Decimal("1500"), True).wallet_credit_applied == Decimal(
        "1500.00"
    )  # capped by remaining
    referral_config.max_credit_per_order = Decimal("300")
    referral_config.save()
    assert services.price_order(student, Decimal("9999"), True).wallet_credit_applied == Decimal("300.00")
    referral_config.enabled = False
    referral_config.save()
    assert services.price_order(student, Decimal("9999"), True).wallet_credit_applied == Decimal("0.00")


def test_on_payment_paid_rewards_referrer_once(
    student, other_student, referred, recorded_course, course, referral_config
):
    first = _payment(student, recorded_course, status="paid")
    services.on_payment_paid(first)
    referred.refresh_from_db()
    assert referred.status == "rewarded"
    assert referred.converted_at is not None
    assert referred.reward_amount == Decimal("500.00")
    assert referred.payment == first
    reward = ReferralReward.objects.get(status="credited")
    assert reward.user == other_student
    assert reward.referral == referred
    assert reward.payment == first
    assert reward.amount == Decimal("500.00")
    assert student.name in reward.note and recorded_course.title in reward.note
    assert services.wallet_balance(other_student) == Decimal("500.00")
    # A second purchase does not reward again.
    second = _payment(student, course, status="paid")
    services.on_payment_paid(second)
    assert ReferralReward.objects.filter(status="credited").count() == 1
    assert services.wallet_balance(other_student) == Decimal("500.00")


def test_on_payment_paid_no_reward_when_referee_paid_before(student, referred, recorded_course, course):
    _payment(student, course, status="paid")  # earlier confirmed purchase
    payment = _payment(student, recorded_course, status="paid")
    services.on_payment_paid(payment)
    referred.refresh_from_db()
    assert referred.status == "signed_up"
    assert ReferralReward.objects.count() == 0


def test_on_payment_paid_respects_programme_toggle(student, referred, recorded_course, referral_config):
    referral_config.enabled = False
    referral_config.save()
    services.on_payment_paid(_payment(student, recorded_course, status="paid"))
    referred.refresh_from_db()
    assert referred.status == "signed_up"
    assert ReferralReward.objects.count() == 0


def test_on_payment_paid_redeems_wallet_credit(student, recorded_course, referral_config):
    ReferralReward.objects.create(user=student, amount=Decimal("900"), status="credited", note="seed")
    payment = _payment(
        student, recorded_course, status="paid", wallet_credit_applied=Decimal("600"), amount=Decimal("9399")
    )
    services.on_payment_paid(payment)
    redeemed = ReferralReward.objects.get(status="redeemed")
    assert redeemed.user == student
    assert redeemed.amount == Decimal("-600.00")
    assert redeemed.payment == payment
    assert payment.order_id in redeemed.note
    assert services.wallet_balance(student) == Decimal("300.00")


def test_on_payment_paid_redeems_at_most_the_balance(student, recorded_course, referral_config):
    ReferralReward.objects.create(user=student, amount=Decimal("100"), status="credited", note="seed")
    payment = _payment(student, recorded_course, status="paid", wallet_credit_applied=Decimal("600"))
    services.on_payment_paid(payment)
    assert ReferralReward.objects.get(status="redeemed").amount == Decimal("-100.00")
    assert services.wallet_balance(student) == Decimal("0.00")


def test_on_payment_refunded_reverses_and_restores(student, other_student, referred, recorded_course, referral_config):
    ReferralReward.objects.create(user=student, amount=Decimal("700"), status="credited", note="seed")
    payment = _payment(student, recorded_course, status="paid", wallet_credit_applied=Decimal("700"))
    services.on_payment_paid(payment)
    assert services.wallet_balance(student) == Decimal("0.00")
    assert services.wallet_balance(other_student) == Decimal("500.00")

    payment.status = "refunded"
    payment.save()
    services.on_payment_refunded(payment)
    referred.refresh_from_db()
    assert referred.status == "cancelled"
    reversed_entry = ReferralReward.objects.get(status="reversed")
    assert reversed_entry.user == other_student
    assert reversed_entry.amount == Decimal("-500.00")
    assert reversed_entry.referral == referred
    restored = ReferralReward.objects.get(user=student, status="credited", payment=payment)
    assert restored.amount == Decimal("700.00")
    assert payment.order_id in restored.note
    assert services.wallet_balance(other_student) == Decimal("0.00")
    assert services.wallet_balance(student) == Decimal("700.00")
    # Refunding again has nothing left to reverse.
    services.on_payment_refunded(payment)
    assert ReferralReward.objects.filter(status="reversed").count() == 1
    assert ReferralReward.objects.filter(user=student, status="credited").count() == 2


def test_on_payment_refunded_without_referral_or_credit_is_noop(student, recorded_course):
    payment = _payment(student, recorded_course, status="refunded")
    services.on_payment_refunded(payment)
    assert ReferralReward.objects.count() == 0
