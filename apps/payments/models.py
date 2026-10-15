"""Payments: orders/transactions and idempotent webhook event records."""

from django.conf import settings
from django.db import models

from common.models import PrefixedIDModel


class PaymentTransaction(PrefixedIDModel):
    ID_PREFIX = "pay"

    STATUS_CHOICES = [
        ("pending", "Pending"),
        ("paid", "Paid"),
        ("failed", "Failed"),
        ("refunded", "Refunded"),
        ("cancelled", "Cancelled"),
    ]
    REFUND_CHOICES = [("none", "None"), ("requested", "Requested"), ("refunded", "Refunded")]

    order_id = models.CharField(max_length=32, unique=True)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payments")
    course = models.ForeignKey("courses.Course", on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=12, decimal_places=2)  # payable after discounts/credits
    currency = models.CharField(max_length=3, default="INR")
    list_price = models.DecimalField(max_digits=12, decimal_places=2)
    referral_discount = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    wallet_credit_applied = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="pending", db_index=True)
    refund_status = models.CharField(max_length=16, choices=REFUND_CHOICES, default="none")
    method = models.CharField(max_length=32, blank=True, default="")
    gateway_provider = models.CharField(max_length=32, blank=True, default="")
    gateway_reference = models.CharField(max_length=128, blank=True, default="")
    # Opaque token the client hands to the hosted checkout; carries no secret.
    checkout_token = models.CharField(max_length=64, unique=True)
    invoice_available = models.BooleanField(default=False)
    paid_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=240, blank=True, default="")
    refunded_at = models.DateTimeField(null=True, blank=True)
    refund_reason = models.CharField(max_length=240, blank=True, default="")

    class Meta:
        db_table = "payments_transaction"
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["student", "course", "status"])]
        constraints = [models.CheckConstraint(condition=models.Q(amount__gte=0), name="payment_amount_non_negative")]

    def __str__(self) -> str:
        return f"{self.order_id} {self.status}"


class WebhookEvent(models.Model):
    """
    Every gateway webhook delivery is recorded once by its provider event id, so a
    redelivered event is acknowledged without being re-processed.
    """

    id = models.BigAutoField(primary_key=True)
    provider = models.CharField(max_length=32)
    event_id = models.CharField(max_length=128)
    payment = models.ForeignKey(
        PaymentTransaction, null=True, blank=True, on_delete=models.SET_NULL, related_name="webhook_events"
    )
    event_type = models.CharField(max_length=64, blank=True, default="")
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)
    result = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "payments_webhook_event"
        constraints = [models.UniqueConstraint(fields=["provider", "event_id"], name="webhook_event_unique")]
