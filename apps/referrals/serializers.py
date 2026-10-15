"""Refer & Earn serializers."""

from __future__ import annotations

from rest_framework import serializers

from apps.referrals.models import Referral, ReferralProgramConfig, ReferralReward
from common.utils import mask_email


class ReferralProgramConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReferralProgramConfig
        fields = ["enabled", "referrer_reward_amount", "referee_discount_amount", "currency", "max_credit_per_order"]
        read_only_fields = fields


class ReferralProgramConfigUpdateSerializer(serializers.Serializer):
    """Amounts are whole numbers between 0 and 100000 (contract)."""

    enabled = serializers.BooleanField(required=False, default=True)
    referrer_reward_amount = serializers.IntegerField(min_value=0, max_value=100_000)
    referee_discount_amount = serializers.IntegerField(min_value=0, max_value=100_000)
    max_credit_per_order = serializers.IntegerField(min_value=0, max_value=100_000)


class ReferralSerializer(serializers.ModelSerializer):
    """Referral shape. Pass context={"mask_emails": True} for the student view."""

    referee_name = serializers.CharField(source="referee.name", read_only=True)
    referee_email = serializers.SerializerMethodField()
    course_title = serializers.SerializerMethodField()

    class Meta:
        model = Referral
        fields = [
            "id",
            "referrer_id",
            "referral_code",
            "referee_id",
            "referee_name",
            "referee_email",
            "status",
            "created_at",
            "converted_at",
            "reward_amount",
            "payment_id",
            "course_title",
        ]
        read_only_fields = fields

    def get_referee_email(self, obj) -> str:
        email = obj.referee.email
        return mask_email(email) if self.context.get("mask_emails") else email

    def get_course_title(self, obj) -> str | None:
        return obj.payment.course.title if obj.payment_id else None


class ReferralAdminRowSerializer(ReferralSerializer):
    referrer_name = serializers.CharField(source="referrer.name", read_only=True)
    referrer_email = serializers.EmailField(source="referrer.email", read_only=True)

    class Meta(ReferralSerializer.Meta):
        fields = [*ReferralSerializer.Meta.fields, "referrer_name", "referrer_email"]
        read_only_fields = fields


class ReferralRewardSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReferralReward
        fields = ["id", "referral_id", "user_id", "amount", "currency", "status", "note", "created_at"]
        read_only_fields = fields


class ReferralStatsSerializer(serializers.Serializer):
    signed_up = serializers.IntegerField()
    converted = serializers.IntegerField()
    total_earned = serializers.DecimalField(max_digits=12, decimal_places=2)
    wallet_balance = serializers.DecimalField(max_digits=12, decimal_places=2)


class ReferralSummarySerializer(serializers.Serializer):
    program = ReferralProgramConfigSerializer()
    referral_code = serializers.CharField()
    referral_link = serializers.CharField()
    stats = ReferralStatsSerializer()
    referrals = ReferralSerializer(many=True)
    rewards = ReferralRewardSerializer(many=True)


class ReferralCodeValidationSerializer(serializers.Serializer):
    valid = serializers.BooleanField()
    referrer_first_name = serializers.CharField(allow_null=True)
    referee_discount_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    currency = serializers.CharField()


class ReferralAdminStatsSerializer(serializers.Serializer):
    total_referrals = serializers.IntegerField()
    converted = serializers.IntegerField()
    conversion_rate = serializers.IntegerField()
    rewards_credited = serializers.DecimalField(max_digits=12, decimal_places=2)
    rewards_redeemed = serializers.DecimalField(max_digits=12, decimal_places=2)
    outstanding_balance = serializers.DecimalField(max_digits=12, decimal_places=2)
    currency = serializers.CharField()
