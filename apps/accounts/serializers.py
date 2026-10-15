"""User / student profile serializers (snake_case here -> camelCase on the wire)."""

from __future__ import annotations

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import URLValidator
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import User
from common.permissions import ROLES
from common.validators import mobile_validator

AVATAR_DATA_URL_MAX_CHARS = 400_000
AVATAR_HTTPS_URL_MAX_CHARS = 2_048

_https_validator = URLValidator(schemes=["https"])


class UserSerializer(serializers.ModelSerializer):
    """Public representation of a user (matches ``User`` in src/types/auth.ts)."""

    role = serializers.ChoiceField(choices=ROLES, read_only=True)
    permissions = serializers.ListField(child=serializers.CharField(), read_only=True)
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ("id", "name", "email", "mobile", "role", "permissions", "avatar_url", "email_verified", "created_at")
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_avatar_url(self, obj: User) -> str | None:
        return obj.avatar_url or None


class NotificationPreferencesSerializer(serializers.Serializer):
    live_class_reminders = serializers.BooleanField(required=False)
    course_updates = serializers.BooleanField(required=False)
    payment_receipts = serializers.BooleanField(required=False)
    marketing = serializers.BooleanField(required=False)


class StudentProfileSerializer(UserSerializer):
    """``StudentProfile`` = User + date of birth, address and notification preferences."""

    address = serializers.SerializerMethodField()
    notification_preferences = serializers.SerializerMethodField()

    class Meta(UserSerializer.Meta):
        fields = (*UserSerializer.Meta.fields, "date_of_birth", "address", "notification_preferences")
        read_only_fields = fields

    @extend_schema_field(serializers.CharField(allow_null=True))
    def get_address(self, obj: User) -> str | None:
        return obj.address or None

    @extend_schema_field(NotificationPreferencesSerializer)
    def get_notification_preferences(self, obj: User) -> dict:
        return {
            "live_class_reminders": obj.notify_live_class_reminders,
            "course_updates": obj.notify_course_updates,
            "payment_receipts": obj.notify_payment_receipts,
            "marketing": obj.notify_marketing,
        }


class UpdateProfileSerializer(serializers.Serializer):
    """``UpdateProfileRequest``. Email and role are deliberately absent: they are not editable here."""

    name = serializers.CharField(required=False, min_length=2, max_length=80)
    mobile = serializers.CharField(required=False, min_length=10, max_length=20, validators=[mobile_validator])
    date_of_birth = serializers.DateField(required=False, allow_null=True)
    address = serializers.CharField(required=False, allow_null=True, allow_blank=True, max_length=240)
    avatar_url = serializers.CharField(
        required=False, allow_null=True, allow_blank=True, max_length=AVATAR_DATA_URL_MAX_CHARS, trim_whitespace=True
    )
    notification_preferences = NotificationPreferencesSerializer(required=False)

    def validate_avatar_url(self, value: str | None) -> str:
        if not value:
            return ""
        if value.startswith("https://"):
            if len(value) > AVATAR_HTTPS_URL_MAX_CHARS:
                raise serializers.ValidationError("Avatar URL is too long.")
            try:
                _https_validator(value)
            except DjangoValidationError:
                raise serializers.ValidationError("Enter a valid https URL.")
            return value
        if value.startswith("data:image/") and ";base64," in value[:64]:
            return value  # max_length already bounds the payload to ~400 KB
        raise serializers.ValidationError("Avatar must be an https URL or an image data URL.")

    def validate_address(self, value: str | None) -> str:
        return (value or "").strip()


class ChangePasswordSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)
    new_password = serializers.CharField(write_only=True, trim_whitespace=False, max_length=128)


class MessageResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
