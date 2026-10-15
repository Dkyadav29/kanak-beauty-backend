"""Throttles for sensitive endpoints. Rates live in REST_FRAMEWORK['DEFAULT_THROTTLE_RATES']."""

from rest_framework.throttling import AnonRateThrottle, SimpleRateThrottle


class LoginThrottle(SimpleRateThrottle):
    """Per-identifier (email + IP) limit for login attempts."""

    scope = "auth_login"

    def get_cache_key(self, request, view):
        email = str(request.data.get("email", "")).lower().strip() if hasattr(request, "data") else ""
        ident = f"{email}|{self.get_ident(request)}"
        return self.cache_format % {"scope": self.scope, "ident": ident}


class RegisterThrottle(AnonRateThrottle):
    scope = "auth_register"


class OtpThrottle(SimpleRateThrottle):
    scope = "auth_otp"

    def get_cache_key(self, request, view):
        email = str(request.data.get("email", "")).lower().strip() if hasattr(request, "data") else ""
        return self.cache_format % {"scope": self.scope, "ident": f"{email}|{self.get_ident(request)}"}


class PasswordResetThrottle(AnonRateThrottle):
    scope = "auth_password_reset"


class PublicLookupThrottle(AnonRateThrottle):
    """Public lookups (certificate verification, referral code validation)."""

    scope = "public_lookup"
