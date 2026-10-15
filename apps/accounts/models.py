"""
Accounts: custom user model with a role, student profile fields and the
supporting records for email verification, password reset and login auditing.
"""

from __future__ import annotations

import secrets

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.utils import timezone

from common.ids import make_id
from common.permissions import ROLE_ADMIN, ROLE_STUDENT, ROLES, permissions_for_role
from common.validators import mobile_validator


def generate_referral_code(name: str) -> str:
    stem = "".join(ch for ch in (name.split(" ")[0] if name else "KANAK").upper() if ch.isalpha())[:6] or "KANAK"
    return f"{stem}-{secrets.token_hex(2).upper()}"


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, email: str, password: str | None, **extra):
        if not email:
            raise ValueError("Email is required")
        email = self.normalize_email(email).lower()
        user = self.model(email=email, **extra)
        user.set_password(password)
        if not user.referral_code:
            user.referral_code = self._unique_referral_code(user.name)
        user.save(using=self._db)
        return user

    def _unique_referral_code(self, name: str) -> str:
        for _ in range(20):
            code = generate_referral_code(name)
            if not self.model.objects.filter(referral_code=code).exists():
                return code
        return f"KANAK-{secrets.token_hex(4).upper()}"

    def create_user(self, email, password=None, **extra):
        extra.setdefault("role", ROLE_STUDENT)
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.update(role=ROLE_ADMIN, is_staff=True, is_superuser=True, email_verified=True)
        return self._create(email, password, **extra)


class User(AbstractBaseUser, PermissionsMixin):
    """
    Application user. ``role`` drives API authorization (see common.permissions).
    Django's is_staff/is_superuser are only used for the optional Django admin site.
    """

    ROLE_CHOICES = [(r, r.title()) for r in ROLES]

    id = models.CharField(primary_key=True, max_length=40, editable=False)
    email = models.EmailField(unique=True)
    name = models.CharField(max_length=120)
    mobile = models.CharField(max_length=20, validators=[mobile_validator])
    role = models.CharField(max_length=16, choices=ROLE_CHOICES, default=ROLE_STUDENT, db_index=True)
    avatar_url = models.TextField(blank=True, default="")
    email_verified = models.BooleanField(default=False)
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    # Student profile (kept on the user for simplicity; only exposed to the owner/admin)
    date_of_birth = models.DateField(null=True, blank=True)
    address = models.CharField(max_length=240, blank=True, default="")
    notify_live_class_reminders = models.BooleanField(default=True)
    notify_course_updates = models.BooleanField(default=True)
    notify_payment_receipts = models.BooleanField(default=True)
    notify_marketing = models.BooleanField(default=False)

    # Refer & Earn
    referral_code = models.CharField(max_length=24, unique=True)
    referred_by = models.ForeignKey(
        "self", null=True, blank=True, on_delete=models.SET_NULL, related_name="referred_users"
    )

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = UserManager()

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = ["name", "mobile"]

    class Meta:
        db_table = "accounts_user"
        indexes = [models.Index(fields=["role", "is_active"])]

    def __str__(self) -> str:
        return f"{self.name} <{self.email}>"

    def save(self, *args, **kwargs):
        if not self.id:
            self.id = make_id("usr")
        if not self.referral_code:
            self.referral_code = User.objects._unique_referral_code(self.name)
        super().save(*args, **kwargs)

    @property
    def permissions(self) -> list[str]:
        return permissions_for_role(self.role)

    @property
    def is_student(self) -> bool:
        return self.role == ROLE_STUDENT

    @property
    def is_admin_role(self) -> bool:
        return self.role == ROLE_ADMIN


class OneTimeCode(models.Model):
    """Email verification OTPs. Only a hash of the code is stored."""

    PURPOSE_VERIFY_EMAIL = "verify_email"
    PURPOSE_CHOICES = [(PURPOSE_VERIFY_EMAIL, "Verify email")]

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="one_time_codes")
    purpose = models.CharField(max_length=32, choices=PURPOSE_CHOICES, default=PURPOSE_VERIFY_EMAIL)
    code_hash = models.CharField(max_length=128)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_one_time_code"
        indexes = [models.Index(fields=["user", "purpose", "expires_at"])]


class PasswordResetToken(models.Model):
    """Single-use password reset tokens (hash stored, raw token emailed)."""

    id = models.BigAutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="password_reset_tokens")
    token_hash = models.CharField(max_length=128, unique=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "accounts_password_reset_token"


class LoginAttempt(models.Model):
    """Security audit trail for authentication attempts (no passwords stored)."""

    id = models.BigAutoField(primary_key=True)
    email = models.EmailField(db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=255, blank=True, default="")
    successful = models.BooleanField(default=False)
    reason = models.CharField(max_length=64, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "accounts_login_attempt"
