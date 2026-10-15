"""Admin reporting endpoints: overview, analytics, student directory and detail."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from common.permissions import HasPermission
from common.utils import query_param

from . import services
from .serializers import (
    AdminAnalyticsSerializer,
    AdminOverviewStatsSerializer,
    StudentDetailAdminSerializer,
    StudentDirectoryRowSerializer,
)

TAGS = ["reports"]

_RANGE_PARAMS = [
    OpenApiParameter("from", str, description="Start (YYYY-MM-DD or ISO datetime), inclusive"),
    OpenApiParameter("to", str, description="End date (YYYY-MM-DD or ISO datetime), inclusive through that day"),
]


@extend_schema(
    tags=TAGS,
    summary="Admin overview statistics",
    parameters=_RANGE_PARAMS,
    responses={200: AdminOverviewStatsSerializer},
)
class AdminOverviewView(APIView):
    permission_classes = [HasPermission]
    required_permission = "analytics:read"

    def get(self, request):
        data = services.overview_stats(from_value=query_param(request, "from"), to_value=query_param(request, "to"))
        return Response(AdminOverviewStatsSerializer(data).data)


@extend_schema(
    tags=TAGS,
    summary="Admin analytics (revenue by month, enrollments by course)",
    parameters=_RANGE_PARAMS,
    responses={200: AdminAnalyticsSerializer},
)
class AdminAnalyticsView(APIView):
    permission_classes = [HasPermission]
    required_permission = "analytics:read"

    def get(self, request):
        return Response(AdminAnalyticsSerializer(services.analytics()).data)


@extend_schema(
    tags=TAGS,
    summary="Student directory",
    parameters=[
        OpenApiParameter("search", str, description="Name, email or mobile"),
        OpenApiParameter("courseId", str),
        OpenApiParameter("batchId", str),
        OpenApiParameter(
            "paymentStatus", str, enum=["pending", "paid", "failed", "refunded", "cancelled", "none", "all"]
        ),
        OpenApiParameter("enrollmentStatus", str, enum=["active", "completed", "none", "all"]),
        OpenApiParameter("page", int),
        OpenApiParameter("pageSize", int),
    ],
    responses={200: StudentDirectoryRowSerializer(many=True)},
)
class AdminStudentListView(ListAPIView):
    permission_classes = [HasPermission]
    required_permission = "students:read"
    serializer_class = StudentDirectoryRowSerializer
    filter_backends: list = []

    def get_queryset(self):
        request = self.request
        return services.student_directory(
            search=query_param(request, "search"),
            course_id=query_param(request, "courseId"),
            batch_id=query_param(request, "batchId"),
            payment_status=query_param(request, "paymentStatus"),
            enrollment_status=query_param(request, "enrollmentStatus"),
        )


@extend_schema(tags=TAGS, summary="Student detail (admin view)", responses={200: StudentDetailAdminSerializer})
class AdminStudentDetailView(APIView):
    permission_classes = [HasPermission]
    required_permission = "students:read"

    def get(self, request, student_id: str):
        student = get_object_or_404(User.objects.filter(role="student"), pk=student_id)
        return Response(StudentDetailAdminSerializer(services.student_detail(student)).data)
