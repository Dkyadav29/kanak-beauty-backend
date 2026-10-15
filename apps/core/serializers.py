"""Serializers for core (audit log)."""

from __future__ import annotations

from rest_framework import serializers

from apps.core.models import AuditLog


class AuditLogSerializer(serializers.ModelSerializer):
    """``AuditLog`` in src/types/admin.ts. System actions (no actor) report ``actorId`` as ``"system"``."""

    actor_id = serializers.SerializerMethodField()

    class Meta:
        model = AuditLog
        fields = ("id", "actor_id", "actor_name", "action", "entity_type", "entity_id", "details", "at")
        read_only_fields = fields

    def get_actor_id(self, obj: AuditLog) -> str:
        return obj.actor_id or "system"
