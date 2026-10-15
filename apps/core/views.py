"""Admin audit-log listing."""

from __future__ import annotations

from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import ListAPIView

from apps.core.models import AuditLog
from apps.core.serializers import AuditLogSerializer
from common.exceptions import ValidationFailed
from common.pagination import PageNumberWithSizePagination
from common.permissions import HasPermission
from common.utils import query_param

TAGS = ["core"]


def _parse_bound(value: str, name: str) -> tuple[str, object]:
    """Return (lookup_suffix, parsed) for a date (``YYYY-MM-DD``) or ISO datetime bound."""
    as_date = parse_date(value)
    if as_date is not None:
        return "date", as_date
    as_dt = parse_datetime(value)
    if as_dt is None:
        raise ValidationFailed(f"'{name}' must be an ISO date or datetime.", details={name: ["Invalid date"]})
    if timezone.is_naive(as_dt):
        as_dt = timezone.make_aware(as_dt)
    return "datetime", as_dt


@extend_schema(
    tags=TAGS,
    summary="List audit log entries",
    parameters=[
        OpenApiParameter("search", str, description="Matches action, details, actor name or entity id."),
        OpenApiParameter("entityType", str, description="Exact entity type, e.g. course, batch, payment."),
        OpenApiParameter("from", str, description="Inclusive lower bound (YYYY-MM-DD or ISO datetime)."),
        OpenApiParameter("to", str, description="Inclusive upper bound (YYYY-MM-DD or ISO datetime)."),
    ],
    responses=AuditLogSerializer(many=True),
)
class AuditLogListView(ListAPIView):
    permission_classes = [HasPermission]
    required_permission = "audit:read"
    serializer_class = AuditLogSerializer
    pagination_class = PageNumberWithSizePagination
    filter_backends: list = []

    def get_queryset(self):
        qs = AuditLog.objects.all().order_by("-at", "-id")
        search = query_param(self.request, "search")
        if search:
            needle = str(search).strip()
            qs = qs.filter(
                Q(action__icontains=needle)
                | Q(details__icontains=needle)
                | Q(actor_name__icontains=needle)
                | Q(entity_id__icontains=needle)
            )
        entity_type = query_param(self.request, "entityType")
        if entity_type:
            qs = qs.filter(entity_type=str(entity_type).strip())
        lower = query_param(self.request, "from")
        if lower:
            kind, value = _parse_bound(str(lower), "from")
            qs = qs.filter(at__date__gte=value) if kind == "date" else qs.filter(at__gte=value)
        upper = query_param(self.request, "to")
        if upper:
            kind, value = _parse_bound(str(upper), "to")
            qs = qs.filter(at__date__lte=value) if kind == "date" else qs.filter(at__lte=value)
        return qs
