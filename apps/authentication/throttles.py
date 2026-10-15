"""
Auth throttles. DRF's ``SimpleRateThrottle.parse_rate`` only reads the first character
of the period, so the contract rate ``"5/10m"`` (5 attempts per 10 minutes) would be
parsed as a KeyError. These subclasses accept ``<n>/<count><unit>`` (e.g. ``5/10m``,
``10/hour``) while reusing the identifier logic from ``common.throttles``.
"""

from __future__ import annotations

import re

from django.core.exceptions import ImproperlyConfigured

from common import throttles as base

_PERIOD_RE = re.compile(r"^\s*(\d*)\s*([smhd])", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}


class MultiPeriodRateMixin:
    def parse_rate(self, rate):
        if rate is None:
            return (None, None)
        try:
            num, period = rate.split("/")
            match = _PERIOD_RE.match(period)
            if not match:
                raise ValueError(period)
            count = int(match.group(1) or 1)
            return int(num), count * _UNIT_SECONDS[match.group(2).lower()]
        except (ValueError, KeyError) as exc:
            raise ImproperlyConfigured(f"Invalid throttle rate '{rate}'") from exc


class LoginThrottle(MultiPeriodRateMixin, base.LoginThrottle):
    pass


class RegisterThrottle(MultiPeriodRateMixin, base.RegisterThrottle):
    pass


class OtpThrottle(MultiPeriodRateMixin, base.OtpThrottle):
    pass


class PasswordResetThrottle(MultiPeriodRateMixin, base.PasswordResetThrottle):
    pass
