"""Student self-service endpoints: profile and password."""

from __future__ import annotations

from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts import services
from apps.accounts.serializers import (
    ChangePasswordSerializer,
    MessageResponseSerializer,
    StudentProfileSerializer,
    UpdateProfileSerializer,
)
from common.permissions import HasPermission, IsStudent

TAGS = ["accounts"]


class StudentProfileView(APIView):
    permission_classes = [IsStudent, HasPermission]

    @property
    def required_permission(self) -> str:
        method = getattr(getattr(self, "request", None), "method", "GET")
        return "profile:update:self" if method in ("PATCH", "PUT") else "profile:read:self"

    @extend_schema(tags=TAGS, summary="Own student profile", responses=StudentProfileSerializer)
    def get(self, request):
        return Response(StudentProfileSerializer(request.user).data)

    @extend_schema(
        tags=TAGS,
        summary="Update own profile (email and role are not editable)",
        request=UpdateProfileSerializer,
        responses=StudentProfileSerializer,
    )
    def patch(self, request):
        serializer = UpdateProfileSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        user = services.update_profile(request.user, serializer.validated_data)
        return Response(StudentProfileSerializer(user).data)


class ChangePasswordView(APIView):
    permission_classes = [IsStudent, HasPermission]
    required_permission = "profile:update:self"

    @extend_schema(
        tags=TAGS,
        summary="Change own password",
        request=ChangePasswordSerializer,
        responses=MessageResponseSerializer,
    )
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.change_password(request.user, request=request, **serializer.validated_data)
        return Response({"message": "Password updated."})
