"""Core: audit log for administrative and security-sensitive actions."""

from django.conf import settings
from django.db import models

from common.models import PrefixedIDModel


class AuditLog(PrefixedIDModel):
    """
    Append-only record of who did what to which entity. Written by
    ``apps.core.services.log_audit``. Never store secrets in ``details``.
    """

    ID_PREFIX = "aud"

    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="audit_logs"
    )
    actor_name = models.CharField(max_length=120)
    action = models.CharField(max_length=64, db_index=True)  # e.g. "course.publish"
    entity_type = models.CharField(max_length=40, db_index=True)  # e.g. "course"
    entity_id = models.CharField(max_length=64, db_index=True)
    details = models.TextField(blank=True, default="")
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "core_audit_log"
        ordering = ["-at"]
        indexes = [models.Index(fields=["entity_type", "entity_id"])]

    def __str__(self) -> str:
        return f"{self.at:%Y-%m-%d %H:%M} {self.actor_name}: {self.action} {self.entity_type}/{self.entity_id}"
