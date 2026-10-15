"""
Refer & Earn services (CONTRACT).

Wallet balance = SUM(ReferralReward.amount) for the user (credits +, redemptions -,
reversals -). All functions below are called from other apps' transactions and
must not open their own outer transaction.

- price_order(student, list_price, apply_wallet_credit) -> OrderPricing: referee
  first-purchase discount (only if programme enabled, student.referred_by set and
  the student has no prior paid/refunded payment), then wallet credit
  min(balance, max_credit_per_order, remaining).
- on_payment_paid(payment): redeem reserved wallet credit (negative 'redeemed'
  entry) and, if the payer has a 'signed_up' Referral and this is their first paid
  payment, mark it rewarded and credit referrer_reward_amount to the referrer.
- on_payment_refunded(payment): reverse the referrer reward (referral ->
  cancelled, negative 'reversed' entry) and restore redeemed credit.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from django.db.models import Q, Sum
from django.utils import timezone

from apps.referrals.models import Referral, ReferralProgramConfig, ReferralReward

ZERO = Decimal("0.00")
CENTS = Decimal("0.01")


def _money(value) -> Decimal:
    return Decimal(value).quantize(CENTS)


@dataclass
class OrderPricing:
    list_price: Decimal
    referral_discount: Decimal
    wallet_credit_applied: Decimal
    amount: Decimal


def wallet_balance(user) -> Decimal:
    total = ReferralReward.objects.filter(user=user).aggregate(total=Sum("amount"))["total"]
    return _money(total or 0)


def find_referrer_by_code(code: str):
    """Active user owning the (case-insensitive) referral code, or None."""
    from apps.accounts.models import User

    normalized = (code or "").strip()
    if not normalized:
        return None
    return User.objects.filter(referral_code__iexact=normalized, is_active=True).first()


def register_referral(referee, code: str):
    """Attach referee to the referrer (sets referred_by, creates Referral). Raises ValidationFailed on bad code."""
    from common.exceptions import ValidationFailed

    def _invalid() -> ValidationFailed:
        return ValidationFailed(
            "That referral code is not valid.",
            code="INVALID_REFERRAL_CODE",
            details={"referral_code": ["Invalid referral code"]},
        )

    referrer = find_referrer_by_code(code)
    if referrer is None or not ReferralProgramConfig.get().enabled:
        raise _invalid()
    # Self-referral is impossible: same account, same email or same mobile number.
    same_mobile = bool(referee.mobile and referrer.mobile) and referrer.mobile.replace(
        " ", ""
    ) == referee.mobile.replace(" ", "")
    if referrer.pk == referee.pk or referrer.email.lower() == (referee.email or "").lower() or same_mobile:
        raise _invalid()
    if Referral.objects.filter(referee=referee).exists():
        raise _invalid()

    referee.referred_by = referrer
    referee.save(update_fields=["referred_by", "updated_at"])
    return Referral.objects.create(
        referrer=referrer,
        referee=referee,
        referral_code=referrer.referral_code,
        status="signed_up",
    )


def has_paid_before(student, *, exclude_payment_id: str | None = None) -> bool:
    """True when the student already has a confirmed (paid or later refunded) payment."""
    from apps.payments.models import PaymentTransaction

    qs = PaymentTransaction.objects.filter(student=student, status__in=("paid", "refunded"))
    if exclude_payment_id:
        qs = qs.exclude(pk=exclude_payment_id)
    return qs.exists()


def price_order(student, list_price: Decimal, apply_wallet_credit: bool) -> OrderPricing:
    cfg = ReferralProgramConfig.get()
    list_price = _money(list_price)
    referrer = student.referred_by if student.referred_by_id else None
    eligible_for_discount = cfg.enabled and referrer is not None and referrer.is_active and not has_paid_before(student)
    referral_discount = min(_money(cfg.referee_discount_amount), list_price) if eligible_for_discount else ZERO
    after_discount = list_price - referral_discount
    if apply_wallet_credit and cfg.enabled:
        wallet_credit = max(ZERO, min(wallet_balance(student), _money(cfg.max_credit_per_order), after_discount))
    else:
        wallet_credit = ZERO
    return OrderPricing(
        list_price=list_price,
        referral_discount=_money(referral_discount),
        wallet_credit_applied=_money(wallet_credit),
        amount=_money(after_discount - wallet_credit),
    )


def on_payment_paid(payment) -> None:
    now = timezone.now()
    # 1. Redeem any wallet credit reserved on the order.
    if payment.wallet_credit_applied and payment.wallet_credit_applied > 0:
        redeem = min(_money(payment.wallet_credit_applied), max(ZERO, wallet_balance(payment.student)))
        if redeem > 0:
            ReferralReward.objects.create(
                referral=None,
                user=payment.student,
                payment=payment,
                amount=-redeem,
                currency=payment.currency,
                status="redeemed",
                note=f"Credit applied to order {payment.order_id}.",
            )
    # 2. Reward the referrer on the referee's first confirmed purchase (at most once per referral).
    cfg = ReferralProgramConfig.get()
    if not cfg.enabled:
        return
    referral = (
        Referral.objects.select_for_update()
        .filter(referee=payment.student, status="signed_up")
        .select_related("referrer", "referee")
        .first()
    )
    if referral is None or has_paid_before(payment.student, exclude_payment_id=payment.id):
        return
    referrer = referral.referrer
    if referrer is None or not referrer.is_active:
        return
    course_title = payment.course.title
    referral.status = "rewarded"
    referral.converted_at = now
    referral.reward_amount = _money(cfg.referrer_reward_amount)
    referral.payment = payment
    referral.save(update_fields=["status", "converted_at", "reward_amount", "payment", "updated_at"])
    ReferralReward.objects.create(
        referral=referral,
        user=referrer,
        payment=payment,
        amount=referral.reward_amount,
        currency=cfg.currency,
        status="credited",
        note=f"Reward for {referral.referee.name}'s first enrollment ({course_title}).",
    )


def on_payment_refunded(payment) -> None:
    referral = (
        Referral.objects.select_for_update()
        .filter(payment=payment, status="rewarded")
        .select_related("referrer")
        .first()
    )
    if referral is not None and referral.reward_amount:
        referral.status = "cancelled"
        referral.save(update_fields=["status", "updated_at"])
        ReferralReward.objects.create(
            referral=referral,
            user=referral.referrer,
            payment=payment,
            amount=-_money(referral.reward_amount),
            currency=payment.currency,
            status="reversed",
            note=f"Reward reversed: order {payment.order_id} was refunded.",
        )
    redeemed = (
        ReferralReward.objects.filter(user=payment.student, status="redeemed")
        .filter(Q(payment=payment) | Q(note__contains=payment.order_id))
        .order_by("created_at")
        .first()
    )
    already_restored = ReferralReward.objects.filter(
        user=payment.student, status="credited", payment=payment, note__startswith="Credit restored"
    ).exists()
    if redeemed is not None and not already_restored:
        ReferralReward.objects.create(
            referral=None,
            user=payment.student,
            payment=payment,
            amount=-redeemed.amount,
            currency=payment.currency,
            status="credited",
            note=f"Credit restored after refund of {payment.order_id}.",
        )
