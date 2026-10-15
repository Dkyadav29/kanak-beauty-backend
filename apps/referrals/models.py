"""Refer & Earn: programme configuration, referrals and the wallet ledger."""

from django.conf import settings
from django.db import models

from common.models import PrefixedIDModel


class ReferralProgramConfig(models.Model):
    """Singleton (pk=1). Use ``ReferralProgramConfig.get()``."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    enabled = models.BooleanField(default=True)
    referrer_reward_amount = models.DecimalField(max_digits=12, decimal_places=2, default=500)
    referee_discount_amount = models.DecimalField(max_digits=12, decimal_places=2, default=300)
    max_credit_per_order = models.DecimalField(max_digits=12, decimal_places=2, default=2000)
    currency = models.CharField(max_length=3, default="INR")
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "referrals_program_config"

    @classmethod
    def get(cls) -> "ReferralProgramConfig":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def save(self, *args, **kwargs):
        self.pk = 1
        super().save(*args, **kwargs)


class Referral(PrefixedIDModel):
    ID_PREFIX = "ref"

    STATUS_CHOICES = [("signed_up", "Signed up"), ("rewarded", "Rewarded"), ("cancelled", "Cancelled")]

    referrer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="referrals_made")
    referee = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="referral")
    referral_code = models.CharField(max_length=24)  # snapshot of the code used
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="signed_up", db_index=True)
    converted_at = models.DateTimeField(null=True, blank=True)
    reward_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    payment = models.ForeignKey(
        "payments.PaymentTransaction", null=True, blank=True, on_delete=models.SET_NULL, related_name="referrals"
    )

    class Meta:
        db_table = "referrals_referral"
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(condition=~models.Q(referrer=models.F("referee")), name="referral_no_self_referral")
        ]


class ReferralReward(PrefixedIDModel):
    """
    Append-only wallet ledger. ``amount`` is signed: credits positive,
    redemptions/reversals negative. Balance = SUM(amount) per user.
    """

    ID_PREFIX = "rwd"

    STATUS_CHOICES = [("credited", "Credited"), ("redeemed", "Redeemed"), ("reversed", "Reversed")]

    referral = models.ForeignKey(Referral, null=True, blank=True, on_delete=models.SET_NULL, related_name="rewards")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="wallet_entries")
    payment = models.ForeignKey(
        "payments.PaymentTransaction", null=True, blank=True, on_delete=models.SET_NULL, related_name="wallet_entries"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES)
    note = models.CharField(max_length=240)

    class Meta:
        db_table = "referrals_reward"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["user", "status"])]
