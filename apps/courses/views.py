"""Public catalogue and admin course/trainer endpoints."""

from __future__ import annotations

from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.courses import selectors, services
from apps.courses.models import Course, Trainer
from apps.courses.serializers import (
    AcademyStatsSerializer,
    CourseSerializer,
    CourseSummarySerializer,
    CourseUpsertSerializer,
    CurriculumUpdateSerializer,
    EnrolledStudentSerializer,
    PublicLiveSessionSerializer,
    ThumbnailResponseSerializer,
    ThumbnailUploadSerializer,
    TrainerSerializer,
    TrainerUpsertSerializer,
)
from common.permissions import HasPermission
from common.utils import query_bool, query_param

PUBLIC_TAG = ["catalogue"]
ADMIN_TAG = ["courses"]


class _SettingsWithoutFormatOverride:
    """api_settings proxy with URL_FORMAT_OVERRIDE disabled."""

    URL_FORMAT_OVERRIDE = None

    def __getattr__(self, name):
        return getattr(api_settings, name)


class CatalogueContentNegotiation(DefaultContentNegotiation):
    """
    The catalogue contract uses ``?format=live|recorded|hybrid`` as a *filter*. DRF would otherwise
    treat ``format`` as a renderer override and answer 404, so the override is disabled here.
    """

    settings = _SettingsWithoutFormatOverride()


def _admin_course_response(course: Course, http_status: int = status.HTTP_200_OK) -> Response:
    fresh = selectors.get_course_for_admin(course.pk)
    return Response(CourseSerializer(fresh, context={"include_materials": True}).data, status=http_status)


# ---------------------------------------------------------------------------
# Public catalogue
# ---------------------------------------------------------------------------


class PublicCatalogueView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [AnonRateThrottle]
    content_negotiation_class = CatalogueContentNegotiation


class PublicCourseListView(PublicCatalogueView):
    @extend_schema(
        summary="List published courses",
        tags=PUBLIC_TAG,
        parameters=[
            OpenApiParameter(
                "search", OpenApiTypes.STR, description="Matches title, short description or trainer name."
            ),
            OpenApiParameter("level", OpenApiTypes.STR, enum=[c[0] for c in Course.LEVEL_CHOICES] + ["all"]),
            OpenApiParameter("format", OpenApiTypes.STR, enum=[c[0] for c in Course.FORMAT_CHOICES] + ["all"]),
            OpenApiParameter("featured", OpenApiTypes.BOOL, description="true returns featured courses only."),
        ],
        responses=CourseSummarySerializer(many=True),
    )
    def get(self, request):
        qs = selectors.course_summaries().filter(selectors.published_filter())
        qs = selectors.search_courses(qs, query_param(request, "search"), include_description=True)
        level = query_param(request, "level")
        if level:
            qs = qs.filter(level=level)
        fmt = query_param(request, "format")
        if fmt:
            qs = qs.filter(format=fmt)
        if query_bool(request, "featured", default=False):
            qs = qs.filter(featured=True)
        return Response(CourseSummarySerializer(qs, many=True).data)


class PublicCourseDetailView(PublicCatalogueView):
    @extend_schema(summary="Published course detail (materials stripped)", tags=PUBLIC_TAG, responses=CourseSerializer)
    def get(self, request, id_or_slug: str):
        course = selectors.get_published_course(id_or_slug)
        return Response(CourseSerializer(course, context={"include_materials": False}).data)


class PublicCourseSessionsView(PublicCatalogueView):
    @extend_schema(
        summary="Upcoming scheduled live sessions for a published course",
        tags=PUBLIC_TAG,
        responses=PublicLiveSessionSerializer(many=True),
    )
    def get(self, request, id_or_slug: str):
        course = get_object_or_404(
            Course.objects.filter(selectors.published_filter()), selectors.id_or_slug_filter(id_or_slug)
        )
        sessions = selectors.upcoming_sessions_for_course(course)
        return Response(PublicLiveSessionSerializer(sessions, many=True).data)


class PublicTrainerListView(PublicCatalogueView):
    @extend_schema(summary="List active trainers", tags=PUBLIC_TAG, responses=TrainerSerializer(many=True))
    def get(self, request):
        return Response(TrainerSerializer(selectors.active_trainers(), many=True).data)


class AcademyStatsView(PublicCatalogueView):
    @extend_schema(
        summary="Academy statistics computed from real records", tags=PUBLIC_TAG, responses=AcademyStatsSerializer
    )
    def get(self, request):
        response = Response(AcademyStatsSerializer(services.academy_stats()).data)
        response["Cache-Control"] = "public, max-age=300"
        return response


# ---------------------------------------------------------------------------
# Admin: courses
# ---------------------------------------------------------------------------


class AdminCourseView(APIView):
    permission_classes = [HasPermission]
    required_permission = "courses:manage"


class AdminCourseListCreateView(AdminCourseView):
    @extend_schema(
        summary="List courses (all statuses)",
        tags=ADMIN_TAG,
        parameters=[
            OpenApiParameter("search", OpenApiTypes.STR, description="Matches title or trainer name."),
            OpenApiParameter("status", OpenApiTypes.STR, enum=[c[0] for c in Course.STATUS_CHOICES] + ["all"]),
        ],
        responses=CourseSummarySerializer(many=True),
    )
    def get(self, request):
        qs = selectors.course_summaries()
        qs = selectors.search_courses(qs, query_param(request, "search"), include_description=False)
        course_status = query_param(request, "status")
        if course_status:
            qs = qs.filter(status=course_status)
        return Response(CourseSummarySerializer(qs.order_by("-updated_at"), many=True).data)

    @extend_schema(
        summary="Create a course", tags=ADMIN_TAG, request=CourseUpsertSerializer, responses={201: CourseSerializer}
    )
    def post(self, request):
        serializer = CourseUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = services.create_course(data=serializer.validated_data, actor=request.user, request=request)
        return _admin_course_response(course, status.HTTP_201_CREATED)


class AdminCourseDetailView(AdminCourseView):
    @extend_schema(summary="Course detail with full curriculum", tags=ADMIN_TAG, responses=CourseSerializer)
    def get(self, request, course_id: str):
        return _admin_course_response(selectors.get_course_for_admin(course_id))

    @extend_schema(
        summary="Update a course", tags=ADMIN_TAG, request=CourseUpsertSerializer, responses=CourseSerializer
    )
    def put(self, request, course_id: str):
        course = get_object_or_404(Course, pk=course_id)
        serializer = CourseUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.update_course(course=course, data=serializer.validated_data, actor=request.user, request=request)
        return _admin_course_response(course)


class AdminCoursePublishView(AdminCourseView):
    @extend_schema(
        summary="Publish a course (409 EMPTY_CURRICULUM without lessons)",
        tags=ADMIN_TAG,
        request=None,
        responses=CourseSerializer,
    )
    def post(self, request, course_id: str):
        course = get_object_or_404(Course, pk=course_id)
        services.publish_course(course=course, actor=request.user, request=request)
        return _admin_course_response(course)


class AdminCourseUnpublishView(AdminCourseView):
    @extend_schema(summary="Unpublish a course", tags=ADMIN_TAG, request=None, responses=CourseSerializer)
    def post(self, request, course_id: str):
        course = get_object_or_404(Course, pk=course_id)
        services.unpublish_course(course=course, actor=request.user, request=request)
        return _admin_course_response(course)


class AdminCourseCurriculumView(AdminCourseView):
    @extend_schema(
        summary="Replace the curriculum structure (ids preserved)",
        tags=ADMIN_TAG,
        request=CurriculumUpdateSerializer,
        responses=CourseSerializer,
    )
    def put(self, request, course_id: str):
        course = get_object_or_404(Course, pk=course_id)
        serializer = CurriculumUpdateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.replace_curriculum(
            course=course, modules=serializer.validated_data["modules"], actor=request.user, request=request
        )
        return _admin_course_response(course)


class AdminCourseThumbnailView(AdminCourseView):
    parser_classes = [MultiPartParser, FormParser]

    @extend_schema(
        summary="Upload a course thumbnail",
        tags=ADMIN_TAG,
        request={"multipart/form-data": ThumbnailUploadSerializer},
        responses=ThumbnailResponseSerializer,
    )
    def post(self, request, course_id: str):
        course = get_object_or_404(Course, pk=course_id)
        serializer = ThumbnailUploadSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        url = services.store_course_thumbnail(
            course=course, uploaded_file=serializer.validated_data["file"], actor=request.user, request=request
        )
        return Response(ThumbnailResponseSerializer({"url": url}).data)


class AdminCourseEnrolledStudentsView(APIView):
    permission_classes = [HasPermission]
    required_permission = "batches:manage"

    @extend_schema(
        summary="Students with active access to a course",
        tags=ADMIN_TAG,
        responses=EnrolledStudentSerializer(many=True),
    )
    def get(self, request, course_id: str):
        course = get_object_or_404(Course, pk=course_id)
        return Response(EnrolledStudentSerializer(services.enrolled_students(course), many=True).data)


# ---------------------------------------------------------------------------
# Admin: trainers
# ---------------------------------------------------------------------------


class AdminTrainerListCreateView(AdminCourseView):
    @extend_schema(summary="List trainers", tags=ADMIN_TAG, responses=TrainerSerializer(many=True))
    def get(self, request):
        return Response(TrainerSerializer(Trainer.objects.all(), many=True).data)

    @extend_schema(
        summary="Create a trainer", tags=ADMIN_TAG, request=TrainerUpsertSerializer, responses={201: TrainerSerializer}
    )
    def post(self, request):
        serializer = TrainerUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        trainer = services.create_trainer(data=serializer.validated_data, actor=request.user, request=request)
        return Response(TrainerSerializer(trainer).data, status=status.HTTP_201_CREATED)


class AdminTrainerDetailView(AdminCourseView):
    @extend_schema(summary="Trainer detail", tags=ADMIN_TAG, responses=TrainerSerializer)
    def get(self, request, trainer_id: str):
        return Response(TrainerSerializer(get_object_or_404(Trainer, pk=trainer_id)).data)

    @extend_schema(
        summary="Update a trainer", tags=ADMIN_TAG, request=TrainerUpsertSerializer, responses=TrainerSerializer
    )
    def put(self, request, trainer_id: str):
        trainer = get_object_or_404(Trainer, pk=trainer_id)
        serializer = TrainerUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.update_trainer(trainer=trainer, data=serializer.validated_data, actor=request.user, request=request)
        return Response(TrainerSerializer(trainer).data)
