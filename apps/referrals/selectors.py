"""Read-side helpers for Refer & Earn views (student summary, admin list/stats)."""

from __future__ import annotations

from decimal import Decimal
from urllib.parse import quote

from django.db.models import Q, Sum

from apps.referrals import services
from apps.referrals.models import Referral, ReferralProgramConfig, ReferralReward
from common.permissions import ROLE_STUDENT
from common.utils import public_origin

ZERO = Decimal("0.00")


def referrals_queryset():
    return Referral.objects.select_related("referrer", "referee", "payment__course").order_by("-created_at")


def student_summary(user, request=None) -> dict:
    cfg = ReferralProgramConfig.get()
    referrals = list(referrals_queryset().filter(referrer=user))
    rewards = list(ReferralReward.objects.filter(user=user).order_by("-created_at"))
    total_earned = sum((r.amount for r in rewards if r.status == "credited" and r.referral_id), ZERO)
    return {
        "program": cfg,
        "referral_code": user.referral_code,
        "referral_link": f"{public_origin(request)}/register?ref={quote(user.referral_code, safe='')}",
        "stats": {
            "signed_up": len(referrals),
            "converted": sum(1 for r in referrals if r.status == "rewarded"),
            "total_earned": total_earned,
            "wallet_balance": services.wallet_balance(user),
        },
        "referrals": referrals,
        "rewards": rewards,
    }


def filter_admin_referrals(queryset, *, search=None, status=None):
    if status:
        queryset = queryset.filter(status=status)
    if search:
        queryset = queryset.filter(
            Q(referrer__name__icontains=search)
            | Q(referrer__email__icontains=search)
            | Q(referee__name__icontains=search)
            | Q(referee__email__icontains=search)
            | Q(referral_code__icontains=search)
        )
    return queryset


def admin_stats() -> dict:
    cfg = ReferralProgramConfig.get()
    total = Referral.objects.count()
    converted = Referral.objects.filter(status="rewarded").count()
    credited = (
        ReferralReward.objects.filter(status="credited", referral__isnull=False).aggregate(t=Sum("amount"))["t"] or ZERO
    )
    redeemed = ReferralReward.objects.filter(status="redeemed").aggregate(t=Sum("amount"))["t"] or ZERO
    balances = (
        ReferralReward.objects.filter(user__role=ROLE_STUDENT)
        .values("user_id")
        .annotate(balance=Sum("amount"))
        .values_list("balance", flat=True)
    )
    outstanding = sum((b for b in balances if b and b > 0), ZERO)
    return {
        "total_referrals": total,
        "converted": converted,
        "conversion_rate": (200 * converted + total) // (2 * total) if total else 0,
        "rewards_credited": credited,
        "rewards_redeemed": -redeemed,
        "outstanding_balance": outstanding,
        "currency": cfg.currency,
    }
