"""Audit logging helper used by every admin mutation and security-sensitive operation."""

from __future__ import annotations

from apps.core.models import AuditLog
from common.utils import redact


def client_ip(request) -> str | None:
    if request is None:
        return None
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


def log_audit(*, actor, action: str, entity_type: str, entity_id: str, details: str = "", request=None) -> AuditLog:
    """
    Record an administrative/security action.

    ``actor`` may be None for system actions (webhooks, automatic issuance).
    ``details`` must never contain secrets; callers pass plain prose.
    """
    return AuditLog.objects.create(
        actor=actor if getattr(actor, "pk", None) else None,
        actor_name=getattr(actor, "name", None) or "System",
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        details=str(redact(details)) if not isinstance(details, str) else details,
        ip_address=client_ip(request),
    )
