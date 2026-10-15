"""Request/response serializers for the authentication endpoints (see src/types/auth.ts)."""

from __future__ import annotations

from rest_framework import serializers

from apps.accounts.models import User
from apps.accounts.serializers import MessageResponseSerializer, UserSerializer  # noqa: F401 (re-exported)
from apps.authentication.services import normalize_email, validate_new_password
from common.validators import mobile_validator


class RegisterSerializer(serializers.Serializer):
    """``RegisterRequest``. A ``role`` key in the body is ignored: accounts are always students."""

    name = serializers.CharField(min_length=2, max_length=80)
    email = serializers.EmailField(max_length=254)
    mobile = serializers.CharField(min_length=10, max_length=20, validators=[mobile_validator])
    password = serializers.CharField(write_only=True, max_length=128, trim_whitespace=False)
    referral_code = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=40)

    def validate_email(self, value: str) -> str:
        return normalize_email(value)

    def validate_referral_code(self, value: str | None) -> str:
        return (value or "").strip().upper()

    def validate(self, attrs: dict) -> dict:
        validate_new_password(attrs["password"], User(email=attrs["email"], name=attrs["name"]))
        return attrs


class RegisterResponseSerializer(serializers.Serializer):
    user_id = serializers.CharField()
    email = serializers.EmailField()
    verification_required = serializers.BooleanField()


class EmailSerializer(serializers.Serializer):
    email = serializers.EmailField(max_length=254)

    def validate_email(self, value: str) -> str:
        return normalize_email(value)


class VerifyOtpSerializer(EmailSerializer):
    otp = serializers.CharField(min_length=4, max_length=12, trim_whitespace=True)


class LoginSerializer(EmailSerializer):
    password = serializers.CharField(write_only=True, max_length=128, trim_whitespace=False)


class AuthSessionSerializer(serializers.Serializer):
    """``AuthSession``: tokens plus the public user (with permissions)."""

    access_token = serializers.CharField()
    refresh_token = serializers.CharField()
    expires_at = serializers.DateTimeField()
    user = UserSerializer()


class LogoutSerializer(serializers.Serializer):
    refresh = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class SuccessResponseSerializer(serializers.Serializer):
    success = serializers.BooleanField()


class TokenRefreshRequestSerializer(serializers.Serializer):
    refresh = serializers.CharField()


class TokenRefreshResponseSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    expires_at = serializers.DateTimeField()


class ForgotPasswordResponseSerializer(serializers.Serializer):
    message = serializers.CharField()
    dev_reset_token = serializers.CharField(required=False, help_text="Only present when DEBUG is on.")


class ResetPasswordSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=256, trim_whitespace=True)
    password = serializers.CharField(write_only=True, max_length=128, trim_whitespace=False)
