"""Student profile and password management rules."""

from __future__ import annotations

from django.db import transaction

from apps.accounts.models import User
from apps.authentication.services import blacklist_user_tokens, validate_new_password
from apps.core.services import log_audit
from common.exceptions import ValidationFailed

_PREF_FIELDS = {
    "live_class_reminders": "notify_live_class_reminders",
    "course_updates": "notify_course_updates",
    "payment_receipts": "notify_payment_receipts",
    "marketing": "notify_marketing",
}

_SIMPLE_FIELDS = ("name", "mobile", "date_of_birth", "address", "avatar_url")


def update_profile(user: User, data: dict) -> User:
    """Apply a validated ``UpdateProfileRequest``. Email and role are never touched here."""
    changed: list[str] = []
    for field in _SIMPLE_FIELDS:
        if field in data:
            value = data[field]
            if field == "name":
                value = value.strip()
            setattr(user, field, value)
            changed.append(field)
    for key, model_field in _PREF_FIELDS.items():
        prefs = data.get("notification_preferences") or {}
        if key in prefs:
            setattr(user, model_field, bool(prefs[key]))
            changed.append(model_field)
    if changed:
        user.save(update_fields=[*changed, "updated_at"])
    return user


def change_password(user: User, *, current_password: str, new_password: str, request=None) -> None:
    if not user.check_password(current_password):
        raise ValidationFailed(
            "Your current password is incorrect.",
            code="INVALID_PASSWORD",
            details={"current_password": ["Incorrect"]},
        )
    validate_new_password(new_password, user, field="new_password")
    with transaction.atomic():
        user.set_password(new_password)
        user.save(update_fields=["password", "updated_at"])
        # Other devices must sign in again; the caller's current (newest) refresh token survives.
        blacklist_user_tokens(user, keep_latest=True)
        log_audit(
            actor=user,
            action="auth.password_change",
            entity_type="user",
            entity_id=user.pk,
            details="Password changed by the account owner; other sessions revoked.",
            request=request,
        )
