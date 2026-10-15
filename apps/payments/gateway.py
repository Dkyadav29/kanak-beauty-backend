"""
Gateway adapters.

The backend never marks a payment paid on an admin's say-so: the only sources of
truth are the signed webhook and, for reconciliation, an out-of-band status query
to the provider. Each provider implements ``fetch_status``; the sandbox provider
has no out-of-band record, so reconciliation can never change a sandbox payment.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.conf import settings


@dataclass
class GatewayStatus:
    status: str  # "paid" | "failed" | "pending"
    reference: str = ""
    method: str = ""


class BaseGateway:
    provider = "base"

    def fetch_status(self, payment) -> GatewayStatus | None:
        """Authoritative status from the provider, or None when it cannot be determined."""
        raise NotImplementedError


class SandboxGateway(BaseGateway):
    """Local sandbox: settlements only ever arrive through the (signed) webhook."""

    provider = "sandbox"

    def fetch_status(self, payment) -> GatewayStatus | None:
        return None


# Register real providers here, e.g. "razorpay": RazorpayGateway (implements fetch_status via the provider API).
GATEWAYS: dict[str, type[BaseGateway]] = {"sandbox": SandboxGateway}


def get_gateway() -> BaseGateway:
    return GATEWAYS.get(settings.PAYMENT_GATEWAY, SandboxGateway)()
