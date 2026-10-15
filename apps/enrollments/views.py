"""Student learning endpoints (dashboard, enrollments, course, progress) and admin enrollment management."""

from __future__ import annotations

from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.courses.models import Course
from apps.enrollments import admin_services, selectors, services
from apps.enrollments.serializers import (
    CourseProgressSummarySerializer,
    EnrolledCourseViewSerializer,
    EnrollmentAccessSerializer,
    EnrollmentAdminRowSerializer,
    GrantEnrollmentSerializer,
    StudentCourseSerializer,
    StudentDashboardSerializer,
    UpdateLessonProgressSerializer,
)
from common.exceptions import ForbiddenError, NotFoundError
from common.permissions import HasPermission, IsStudent
from common.utils import query_param

TAG = "enrollments"


def _entitled_course(request, course_id: str) -> Course:
    """Course by id/slug with curriculum prefetched; 404 unknown, 403 NOT_ENROLLED without active access."""
    course = selectors.course_with_curriculum_queryset().filter(Q(pk=course_id) | Q(slug=course_id)).first()
    if course is None:
        raise NotFoundError("Course not found.")
    if not services.is_entitled(request.user, course.id):
        raise ForbiddenError("You are not enrolled in this course.", code="NOT_ENROLLED")
    return course


# --- Student -----------------------------------------------------------------------------------


class StudentDashboardView(APIView):
    permission_classes = [IsStudent]

    @extend_schema(tags=[TAG], summary="Student dashboard", responses=StudentDashboardSerializer)
    def get(self, request):
        return Response(StudentDashboardSerializer(selectors.build_dashboard(request.user)).data)


class StudentEnrollmentListView(APIView):
    permission_classes = [IsStudent, HasPermission]
    required_permission = "courses:read:enrolled"

    @extend_schema(tags=[TAG], summary="My courses", responses=EnrolledCourseViewSerializer(many=True))
    def get(self, request):
        data = selectors.load_student_learning_data(request.user)
        return Response(EnrolledCourseViewSerializer(data.views, many=True).data)


class StudentCourseView(APIView):
    permission_classes = [IsStudent, HasPermission]
    required_permission = "courses:read:enrolled"

    @extend_schema(tags=[TAG], summary="Enrolled course with full curriculum", responses=StudentCourseSerializer)
    def get(self, request, course_id: str):
        course = _entitled_course(request, course_id)
        return Response(StudentCourseSerializer(course).data)


class StudentCourseProgressView(APIView):
    permission_classes = [IsStudent, HasPermission]
    required_permission = "courses:read:enrolled"

    @extend_schema(
        tags=[TAG], summary="Course progress and completion eligibility", responses=CourseProgressSummarySerializer
    )
    def get(self, request, course_id: str):
        course = _entitled_course(request, course_id)
        return Response(CourseProgressSummarySerializer(services.compute_progress(request.user, course)).data)


class StudentLessonProgressView(APIView):
    permission_classes = [IsStudent, HasPermission]
    required_permission = "progress:update:self"

    @extend_schema(
        tags=[TAG],
        summary="Update lesson progress",
        request=UpdateLessonProgressSerializer,
        responses=CourseProgressSummarySerializer,
    )
    def put(self, request, course_id: str, lesson_id: str):
        course = _entitled_course(request, course_id)
        lesson = next((lesson for lesson in services.course_lessons(course) if lesson.id == lesson_id), None)
        if lesson is None:
            raise NotFoundError("Lesson not found.")
        body = UpdateLessonProgressSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        summary = services.update_lesson_progress(
            request.user,
            course,
            lesson,
            completed=body.validated_data.get("completed"),
            last_position_seconds=body.validated_data.get("last_position_seconds"),
        )
        return Response(CourseProgressSummarySerializer(summary).data)


# --- Admin -------------------------------------------------------------------------------------


class AdminEnrollmentListView(ListAPIView):
    permission_classes = [HasPermission]
    required_permission = "enrollments:read"
    serializer_class = EnrollmentAdminRowSerializer

    def get_queryset(self):
        return admin_services.filter_admin_enrollments(
            admin_services.admin_enrollments_queryset(),
            search=query_param(self.request, "search"),
            course_id=query_param(self.request, "courseId"),
            payment_status=query_param(self.request, "paymentStatus"),
            access_status=query_param(self.request, "accessStatus"),
        )

    @extend_schema(
        tags=[TAG],
        summary="List enrollments (admin)",
        parameters=[
            OpenApiParameter("search", str, description="Student name/email, order id or enrollment id"),
            OpenApiParameter("courseId", str),
            OpenApiParameter("paymentStatus", str, description="pending|paid|failed|refunded|cancelled|none|all"),
            OpenApiParameter("accessStatus", str, description="pending_payment|active|suspended|revoked|all"),
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class AdminEnrollmentGrantView(GenericAPIView):
    permission_classes = [HasPermission]
    required_permission = "enrollments:manage"
    serializer_class = GrantEnrollmentSerializer

    @extend_schema(tags=[TAG], summary="Grant course access (admin)", responses={201: EnrollmentAdminRowSerializer})
    def post(self, request):
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        enrollment = admin_services.grant_enrollment(actor=request.user, request=request, **body.validated_data)
        return Response(EnrollmentAdminRowSerializer(enrollment).data, status=status.HTTP_201_CREATED)


class AdminEnrollmentAccessView(GenericAPIView):
    permission_classes = [HasPermission]
    required_permission = "enrollments:manage"
    serializer_class = EnrollmentAccessSerializer

    @extend_schema(tags=[TAG], summary="Suspend or re-activate access (admin)", responses=EnrollmentAdminRowSerializer)
    def post(self, request, enrollment_id: str):
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        enrollment = admin_services.set_enrollment_access(
            actor=request.user, enrollment_id=enrollment_id, request=request, **body.validated_data
        )
        return Response(EnrollmentAdminRowSerializer(enrollment).data)
