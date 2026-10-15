"""Payment serializers (snake_case fields; camelCase on the wire)."""

from __future__ import annotations

from rest_framework import serializers

from apps.payments.models import PaymentTransaction


class _NullableCharField(serializers.CharField):
    def to_representation(self, value):
        return value if value else None


class PaymentTransactionSerializer(serializers.ModelSerializer):
    student_name = serializers.CharField(source="student.name", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    method = _NullableCharField(read_only=True)
    gateway_reference = _NullableCharField(read_only=True)
    failure_reason = _NullableCharField(read_only=True)

    class Meta:
        model = PaymentTransaction
        fields = [
            "id",
            "order_id",
            "student_id",
            "student_name",
            "course_id",
            "course_title",
            "amount",
            "currency",
            "list_price",
            "referral_discount",
            "wallet_credit_applied",
            "status",
            "refund_status",
            "method",
            "gateway_reference",
            "invoice_available",
            "created_at",
            "paid_at",
            "failure_reason",
        ]
        read_only_fields = fields


class CreateOrderRequestSerializer(serializers.Serializer):
    course_id = serializers.CharField()
    apply_wallet_credit = serializers.BooleanField(required=False, default=False)


class GatewayCheckoutSerializer(serializers.Serializer):
    provider = serializers.CharField()
    checkout_token = serializers.CharField()


class CreateOrderResponseSerializer(serializers.Serializer):
    order_id = serializers.CharField()
    payment_id = serializers.CharField()
    amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    currency = serializers.CharField()
    list_price = serializers.DecimalField(max_digits=12, decimal_places=2)
    referral_discount = serializers.DecimalField(max_digits=12, decimal_places=2)
    wallet_credit_applied = serializers.DecimalField(max_digits=12, decimal_places=2)
    gateway = GatewayCheckoutSerializer()


def order_response(payment: PaymentTransaction) -> dict:
    return {
        "order_id": payment.order_id,
        "payment_id": payment.id,
        "amount": payment.amount,
        "currency": payment.currency,
        "list_price": payment.list_price,
        "referral_discount": payment.referral_discount,
        "wallet_credit_applied": payment.wallet_credit_applied,
        "gateway": {"provider": payment.gateway_provider, "checkout_token": payment.checkout_token},
    }


class WebhookPayloadSerializer(serializers.Serializer):
    event_id = serializers.CharField(max_length=128)
    payment_id = serializers.CharField(max_length=40)
    status = serializers.ChoiceField(choices=[("paid", "Paid"), ("failed", "Failed")])
    gateway_reference = serializers.CharField(max_length=128, required=False, allow_blank=True, default="")
    method = serializers.CharField(max_length=32, required=False, allow_blank=True, default="")
    failure_reason = serializers.CharField(max_length=240, required=False, allow_blank=True, default="")


class WebhookAckSerializer(serializers.Serializer):
    received = serializers.BooleanField()
    idempotent = serializers.BooleanField()


class SandboxCompleteSerializer(serializers.Serializer):
    checkout_token = serializers.CharField(max_length=64)
    outcome = serializers.ChoiceField(choices=[("success", "Success"), ("failure", "Failure")])
    method = serializers.CharField(max_length=32, required=False, allow_blank=True, default="UPI")


class SandboxResultSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=PaymentTransaction.STATUS_CHOICES)


class RefundRequestSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=5, max_length=240)


class ReconcileResponseSerializer(serializers.Serializer):
    payment = PaymentTransactionSerializer()
    gateway_status = serializers.CharField()
    changed = serializers.BooleanField()
