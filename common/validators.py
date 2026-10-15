"""Reusable validators."""

import re

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator

MOBILE_RE = re.compile(r"^\+?[0-9 ]{10,15}$")

mobile_validator = RegexValidator(MOBILE_RE, "Enter a valid mobile number (10 to 15 digits, optional +).")
referral_code_validator = RegexValidator(r"^[A-Z0-9]{2,12}-[A-Z0-9]{3,8}$", "Invalid referral code format.")


class LetterAndDigitValidator:
    """Password must contain at least one letter and one digit (matches the frontend rule)."""

    def validate(self, password, user=None):
        if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
            raise ValidationError(
                "Password must include at least one letter and one number.", code="password_no_letter_digit"
            )

    def get_help_text(self):
        return "Your password must include at least one letter and one number."


def validate_image_upload(uploaded_file, max_bytes: int = 1_500_000):
    """Basic upload validation for avatars/thumbnails: type and size."""
    content_type = getattr(uploaded_file, "content_type", "") or ""
    if not content_type.startswith("image/"):
        raise ValidationError("Only image uploads are allowed.")
    if uploaded_file.size > max_bytes:
        raise ValidationError(f"Image must be smaller than {max_bytes // 1_000_000} MB.")
