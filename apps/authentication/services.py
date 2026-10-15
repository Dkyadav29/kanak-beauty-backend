"""
Authentication business rules: registration, email OTP verification, login,
JWT sessions, logout/blacklisting and the forgot/reset password flow.

Secrets are never persisted in clear text: OTPs and reset tokens are stored as
SHA-256 hashes, and nothing in this module logs a password, code or token.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import UTC, datetime, timedelta

from django.conf import settings
from django.contrib.auth.models import update_last_login
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.mail import send_mail
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken
from rest_framework_simplejwt.tokens import RefreshToken

from apps.accounts.models import LoginAttempt, OneTimeCode, PasswordResetToken, User
from apps.core.services import client_ip, log_audit
from apps.referrals.services import register_referral
from common.exceptions import ApiError, ConflictError, ForbiddenError, ValidationFailed
from common.permissions import ROLE_STUDENT

OTP_MAX_ATTEMPTS = 5

RESEND_OTP_MESSAGE = "If the account exists and is unverified, a new code has been sent."
FORGOT_PASSWORD_MESSAGE = "If an account exists for this email, a reset link has been sent."
RESET_PASSWORD_MESSAGE = "Your password has been reset. Please sign in."


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def normalize_email(email: str) -> str:
    return User.objects.normalize_email((email or "").strip()).lower()


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def validate_new_password(password: str, user: User | None, field: str = "password") -> None:
    """Run AUTH_PASSWORD_VALIDATORS; surface failures as a field-level validation error."""
    try:
        validate_password(password, user=user)
    except DjangoValidationError as exc:
        raise serializers.ValidationError({field: list(exc.messages)})


def token_expiry(token) -> datetime:
    return datetime.fromtimestamp(int(token["exp"]), tz=UTC)


def build_session(user: User) -> dict:
    """Issue a refresh/access pair for ``user`` (records an OutstandingToken)."""
    refresh = RefreshToken.for_user(user)
    access = refresh.access_token
    return {
        "access_token": str(access),
        "refresh_token": str(refresh),
        "expires_at": token_expiry(access),
        "user": user,
    }


def blacklist_user_tokens(user: User, *, keep_latest: bool = False) -> int:
    """
    Blacklist outstanding refresh tokens of ``user``. With ``keep_latest`` the most
    recently issued token (the caller's current session, in all likelihood) is kept.
    """
    tokens = OutstandingToken.objects.filter(user=user).order_by("-created_at", "-id")
    if keep_latest:
        tokens = tokens[1:]
    count = 0
    for token in tokens:
        _, created = BlacklistedToken.objects.get_or_create(token=token)
        count += int(created)
    return count


def _send(subject: str, body: str, to: str) -> None:
    send_mail(subject, body, settings.DEFAULT_FROM_EMAIL, [to], fail_silently=False)


# ---------------------------------------------------------------------------
# Registration & OTP
# ---------------------------------------------------------------------------
def issue_otp(user: User) -> None:
    """Create a fresh verification code (invalidating earlier ones) and email it."""
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = timezone.now()
    OneTimeCode.objects.filter(user=user, purpose=OneTimeCode.PURPOSE_VERIFY_EMAIL, consumed_at__isnull=True).update(
        consumed_at=now
    )
    OneTimeCode.objects.create(
        user=user,
        purpose=OneTimeCode.PURPOSE_VERIFY_EMAIL,
        code_hash=hash_secret(code),
        expires_at=now + timedelta(minutes=settings.OTP_TTL_MINUTES),
    )
    _send(
        "Your Kanak Beauty Service verification code",
        (
            f"Hi {user.name},\n\n"
            f"Your verification code is {code}. It expires in {settings.OTP_TTL_MINUTES} minutes.\n\n"
            "If you did not create an account, you can ignore this email."
        ),
        user.email,
    )


def register_user(*, name: str, email: str, mobile: str, password: str, referral_code: str = "") -> User:
    """Create a student account (role is never client-controlled) and send the verification OTP."""
    email = normalize_email(email)
    if User.objects.filter(email__iexact=email).exists():
        raise ConflictError(
            "An account with this email already exists.",
            code="EMAIL_TAKEN",
            details={"email": ["Already registered"]},
        )
    with transaction.atomic():
        user = User.objects.create_user(
            email=email,
            password=password,
            name=name.strip(),
            mobile=mobile.strip(),
            role=ROLE_STUDENT,
            email_verified=False,
        )
        if referral_code:
            register_referral(user, referral_code)
    issue_otp(user)
    return user


def resend_otp(email: str) -> None:
    """Issue a new code only for existing, unverified, active accounts. Never reveals existence."""
    user = User.objects.filter(email__iexact=normalize_email(email), is_active=True).first()
    if user is not None and not user.email_verified:
        issue_otp(user)


def _invalid_otp() -> ValidationFailed:
    return ValidationFailed("The verification code is invalid or has expired.", code="INVALID_OTP")


def verify_otp(email: str, otp: str) -> User:
    """Validate the code, mark the email verified and return the user. 400 INVALID_OTP otherwise."""
    now = timezone.now()
    error: ApiError | None = None
    user: User | None = None
    with transaction.atomic():
        user = User.objects.select_for_update().filter(email__iexact=normalize_email(email)).first()
        if user is None:
            raise _invalid_otp()
        row = (
            OneTimeCode.objects.select_for_update()
            .filter(user=user, purpose=OneTimeCode.PURPOSE_VERIFY_EMAIL, consumed_at__isnull=True)
            .order_by("-created_at", "-id")
            .first()
        )
        if row is None or row.expires_at <= now or row.attempts >= OTP_MAX_ATTEMPTS:
            raise _invalid_otp()
        if not hmac.compare_digest(row.code_hash, hash_secret((otp or "").strip())):
            row.attempts += 1
            if row.attempts >= OTP_MAX_ATTEMPTS:
                row.consumed_at = now  # too many guesses: the code is dead, a new one must be requested
            row.save(update_fields=["attempts", "consumed_at"])
            error = _invalid_otp()  # raised after the transaction so the attempt counter is persisted
        else:
            row.consumed_at = now
            row.save(update_fields=["consumed_at"])
            if not user.email_verified:
                user.email_verified = True
                user.save(update_fields=["email_verified", "updated_at"])
    if error is not None:
        raise error
    if not user.is_active:
        raise ForbiddenError("This account has been disabled. Please contact the academy.", code="ACCOUNT_DISABLED")
    return user


# ---------------------------------------------------------------------------
# Login / logout
# ---------------------------------------------------------------------------
def record_login_attempt(*, email: str, request, successful: bool, reason: str = "") -> LoginAttempt:
    user_agent = (request.META.get("HTTP_USER_AGENT", "") if request is not None else "")[:255]
    return LoginAttempt.objects.create(
        email=email[:254],
        ip_address=client_ip(request),
        user_agent=user_agent,
        successful=successful,
        reason=reason,
    )


def login(*, email: str, password: str, request) -> User:
    """Authenticate with email + password. Writes a LoginAttempt row for every outcome."""
    email = normalize_email(email)
    user = User.objects.filter(email__iexact=email).first()
    if user is None or not user.check_password(password):
        record_login_attempt(email=email, request=request, successful=False, reason="invalid_credentials")
        raise ApiError("Incorrect email or password.", code="INVALID_CREDENTIALS", status_code=401)
    if not user.is_active:
        record_login_attempt(email=email, request=request, successful=False, reason="account_disabled")
        raise ForbiddenError("This account has been disabled. Please contact the academy.", code="ACCOUNT_DISABLED")
    if not user.email_verified:
        record_login_attempt(email=email, request=request, successful=False, reason="email_not_verified")
        raise ForbiddenError("Please verify your email address before signing in.", code="EMAIL_NOT_VERIFIED")
    record_login_attempt(email=email, request=request, successful=True, reason="ok")
    update_last_login(None, user)
    return user


def logout(user: User, refresh: str | None = None) -> None:
    """Blacklist the given refresh token, or every outstanding refresh token of the user."""
    if refresh:
        try:
            token = RefreshToken(refresh)
        except TokenError:
            # Unknown/expired/already blacklisted token: nothing left to revoke for it.
            return
        if str(token.get("user_id")) != str(user.pk):
            # Never let one user revoke another user's token; fall back to own tokens only.
            blacklist_user_tokens(user)
            return
        try:
            token.blacklist()
        except TokenError:
            pass
        return
    blacklist_user_tokens(user)


# ---------------------------------------------------------------------------
# Forgot / reset password
# ---------------------------------------------------------------------------
def forgot_password(email: str) -> str | None:
    """Create a single-use reset token and email the link. Returns the raw token (for DEBUG echo) or None."""
    user = User.objects.filter(email__iexact=normalize_email(email), is_active=True).first()
    if user is None:
        return None
    raw = secrets.token_urlsafe(32)
    now = timezone.now()
    PasswordResetToken.objects.filter(user=user, used_at__isnull=True).update(used_at=now)
    PasswordResetToken.objects.create(
        user=user,
        token_hash=hash_secret(raw),
        expires_at=now + timedelta(minutes=settings.PASSWORD_RESET_TTL_MINUTES),
    )
    link = f"{settings.PUBLIC_SITE_URL.rstrip('/')}/reset-password?token={raw}"
    _send(
        "Reset your Kanak Beauty Service password",
        (
            f"Hi {user.name},\n\n"
            "Use the link below to choose a new password. "
            f"It is valid for {settings.PASSWORD_RESET_TTL_MINUTES} minutes and can be used once.\n\n"
            f"{link}\n\n"
            "If you did not request a password reset, you can ignore this email."
        ),
        user.email,
    )
    return raw


def _invalid_reset_token() -> ValidationFailed:
    return ValidationFailed("This reset link is invalid or has expired.", code="INVALID_TOKEN")


def reset_password(*, token: str, password: str, request=None) -> User:
    now = timezone.now()
    with transaction.atomic():
        row = (
            PasswordResetToken.objects.select_for_update()
            .select_related("user")
            .filter(token_hash=hash_secret((token or "").strip()))
            .first()
        )
        if row is None or row.used_at is not None or row.expires_at <= now:
            raise _invalid_reset_token()
        user = row.user
        validate_new_password(password, user)
        user.set_password(password)
        user.email_verified = True  # following the emailed link proves ownership of the address
        user.save(update_fields=["password", "email_verified", "updated_at"])
        row.used_at = now
        row.save(update_fields=["used_at"])
        blacklist_user_tokens(user)
        log_audit(
            actor=user,
            action="auth.password_reset",
            entity_type="user",
            entity_id=user.pk,
            details="Password reset via emailed link; all sessions revoked.",
            request=request,
        )
    return user
