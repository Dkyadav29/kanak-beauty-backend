"""URL patterns for the core app. Paths have no trailing slash (mounted under /api/v1/)."""

from django.urls import path

from apps.core import views

urlpatterns = [
    path("admin/audit-logs", views.AuditLogListView.as_view(), name="admin-audit-logs"),
]
