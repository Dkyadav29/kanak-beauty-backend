"""Certificate endpoints: public verification, student certificates, admin management."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.generics import ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from apps.courses.models import Course
from common.exceptions import NotFoundError
from common.permissions import HasPermission, IsStudent
from common.throttles import PublicLookupThrottle
from common.utils import query_param

from . import selectors, services
from .models import Certificate
from .serializers import (
    CertificateReasonSerializer,
    CertificateSerializer,
    CertificateVerificationResultSerializer,
    EligibleStudentSerializer,
    IssueCertificateRequestSerializer,
)

TAGS = ["certificates"]


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------
@extend_schema(
    tags=TAGS,
    summary="Verify a certificate number (public)",
    responses={200: CertificateVerificationResultSerializer},
    auth=[],
)
class CertificateVerifyView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [PublicLookupThrottle]

    def get(self, request, number: str):
        result = services.verify_certificate(number, request=request)
        return Response(CertificateVerificationResultSerializer(result).data)


# ---------------------------------------------------------------------------
# Student
# ---------------------------------------------------------------------------
@extend_schema(
    tags=TAGS, summary="My certificates (issued and revoked)", responses={200: CertificateSerializer(many=True)}
)
class StudentCertificateListView(ListAPIView):
    permission_classes = [HasPermission, IsStudent]
    required_permission = "certificates:read:self"
    serializer_class = CertificateSerializer
    pagination_class = None
    filter_backends: list = []

    def get_queryset(self):
        return selectors.certificates_for_student(self.request.user)


@extend_schema(tags=TAGS, summary="One of my certificates", responses={200: CertificateSerializer})
class StudentCertificateDetailView(RetrieveAPIView):
    permission_classes = [HasPermission, IsStudent]
    required_permission = "certificates:read:self"
    serializer_class = CertificateSerializer
    lookup_url_kwarg = "certificate_id"

    def get_queryset(self):
        return selectors.certificates_for_student(self.request.user)

    def get_object(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs[self.lookup_url_kwarg])


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------
@extend_schema(
    tags=TAGS,
    summary="List certificates (admin)",
    parameters=[
        OpenApiParameter("search", str, description="Certificate number, student name or course title"),
        OpenApiParameter("status", str, enum=["issued", "revoked", "all"]),
        OpenApiParameter("courseId", str),
        OpenApiParameter("page", int),
        OpenApiParameter("pageSize", int),
    ],
    responses={200: CertificateSerializer(many=True)},
)
class AdminCertificateListView(ListAPIView):
    permission_classes = [HasPermission]
    required_permission = "certificates:manage"
    serializer_class = CertificateSerializer
    filter_backends: list = []

    def get_queryset(self):
        return selectors.admin_certificates(
            search=query_param(self.request, "search"),
            status=query_param(self.request, "status"),
            course_id=query_param(self.request, "courseId"),
        )


@extend_schema(tags=TAGS, summary="Certificate detail (admin)", responses={200: CertificateSerializer})
class AdminCertificateDetailView(RetrieveAPIView):
    permission_classes = [HasPermission]
    required_permission = "certificates:manage"
    serializer_class = CertificateSerializer
    queryset = Certificate.objects.all()
    lookup_url_kwarg = "certificate_id"

    def get_object(self):
        return get_object_or_404(self.get_queryset(), pk=self.kwargs[self.lookup_url_kwarg])


@extend_schema(
    tags=TAGS,
    summary="Students eligible for a certificate (criteria met, no active certificate)",
    responses={200: EligibleStudentSerializer(many=True)},
)
class EligibleStudentsView(APIView):
    permission_classes = [HasPermission]
    required_permission = "certificates:manage"

    def get(self, request):
        return Response(EligibleStudentSerializer(selectors.eligible_students(), many=True).data)


@extend_schema(
    tags=TAGS,
    summary="Issue a certificate (eligibility re-verified server-side)",
    request=IssueCertificateRequestSerializer,
    responses={201: CertificateSerializer},
)
class IssueCertificateView(APIView):
    permission_classes = [HasPermission]
    required_permission = "certificates:manage"

    def post(self, request):
        body = IssueCertificateRequestSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        student = User.objects.filter(pk=body.validated_data["student_id"], role="student").first()
        if student is None:
            raise NotFoundError("Student not found.")
        course = Course.objects.filter(pk=body.validated_data["course_id"]).first()
        if course is None:
            raise NotFoundError("Course not found.")
        certificate = services.issue_certificate(
            student, course, issued_by_user=request.user, fail_if_issued=True, request=request
        )
        return Response(CertificateSerializer(certificate).data, status=status.HTTP_201_CREATED)


class _CertificateActionView(APIView):
    permission_classes = [HasPermission]
    required_permission = "certificates:manage"

    def _get(self, certificate_id: str) -> Certificate:
        return get_object_or_404(Certificate.objects.all(), pk=certificate_id)

    def _reason(self, request) -> str:
        body = CertificateReasonSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return body.validated_data["reason"]


@extend_schema(
    tags=TAGS,
    summary="Revoke a certificate",
    request=CertificateReasonSerializer,
    responses={200: CertificateSerializer},
)
class RevokeCertificateView(_CertificateActionView):
    def post(self, request, certificate_id: str):
        certificate = self._get(certificate_id)
        reason = self._reason(request)
        certificate = services.revoke_certificate(certificate, reason=reason, actor=request.user, request=request)
        return Response(CertificateSerializer(certificate).data)


@extend_schema(
    tags=TAGS,
    summary="Reissue a revoked certificate (new number)",
    request=CertificateReasonSerializer,
    responses={200: CertificateSerializer},
)
class ReissueCertificateView(_CertificateActionView):
    def post(self, request, certificate_id: str):
        certificate = self._get(certificate_id)
        reason = self._reason(request)
        new_certificate = services.reissue_certificate(certificate, reason=reason, actor=request.user, request=request)
        return Response(CertificateSerializer(new_certificate).data)
