"""Views for public/student live sessions and recordings, signed media streaming, and admin batch management."""

from __future__ import annotations

import mimetypes
from datetime import datetime, time, timedelta
from pathlib import Path

from django.conf import settings
from django.core import signing
from django.http import FileResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.batches import selectors, services
from apps.batches.models import Batch, LiveSession, Recording
from apps.batches.serializers import (
    AddBatchStudentSerializer,
    AttendanceRequestSerializer,
    AttendanceResponseSerializer,
    BatchDetailSerializer,
    BatchEnrollmentSerializer,
    BatchSerializer,
    BatchStatusChangeSerializer,
    BatchSummarySerializer,
    BatchUpsertSerializer,
    JoinSessionResponseSerializer,
    LiveSessionSerializer,
    LiveSessionUpsertSerializer,
    RecordingPlaybackResponseSerializer,
    RecordingSerializer,
    RecordingUpsertSerializer,
    StudentLiveSessionSerializer,
    WithdrawBatchStudentSerializer,
    student_or_none,
)
from common.exceptions import ApiError, NotFoundError, ValidationFailed
from common.pagination import PageNumberWithSizePagination
from common.permissions import HasPermission, IsStudent
from common.utils import query_param

TAG_LIVE = "live-sessions"
TAG_RECORDINGS = "recordings"
TAG_BATCHES = "batches"


def _parse_date_param(request, name: str):
    raw = query_param(request, name)
    if raw is None:
        return None
    value = parse_date(str(raw)[:10])
    if value is None:
        raise ValidationFailed(f"{name} must be an ISO date (YYYY-MM-DD).", details={name: ["Invalid date."]})
    return value


def _parse_datetime_param(request, name: str, *, end_of_day: bool = False):
    """
    Accept a date or datetime. Dates become local midnight; with ``end_of_day`` the
    result is an exclusive upper bound (next midnight for a date, +1s for a datetime).
    """
    raw = query_param(request, name)
    if raw is None:
        return None
    text = str(raw)
    dt = parse_datetime(text)
    if dt is None:
        day = parse_date(text[:10])
        if day is None:
            raise ValidationFailed(f"{name} must be an ISO date or datetime.", details={name: ["Invalid date."]})
        if end_of_day:
            day = day + timedelta(days=1)
        dt = datetime.combine(day, time.min)
    elif end_of_day:
        dt = dt + timedelta(seconds=1)
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _parse_limit(request, default: int, maximum: int = 100) -> int:
    raw = query_param(request, "limit")
    if raw is None:
        return default
    try:
        return max(1, min(int(raw), maximum))
    except (TypeError, ValueError):
        raise ValidationFailed("limit must be an integer.", details={"limit": ["Invalid number."]})


# ---------------------------------------------------------------------------
# Public
# ---------------------------------------------------------------------------


class UpcomingLiveSessionsView(APIView):
    """GET /live-sessions/upcoming?limit"""

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=[TAG_LIVE],
        summary="Upcoming live sessions across published courses (next 45 days)",
        parameters=[
            OpenApiParameter("limit", OpenApiTypes.INT, OpenApiParameter.QUERY, description="Max rows (default 12)")
        ],
        responses=LiveSessionSerializer(many=True),
    )
    def get(self, request):
        limit = _parse_limit(request, default=12)
        return Response(LiveSessionSerializer(selectors.public_upcoming_sessions(limit), many=True).data)


# ---------------------------------------------------------------------------
# Student
# ---------------------------------------------------------------------------


class StudentLiveSessionsView(APIView):
    """GET /student/live-sessions?scope=upcoming|past|all"""

    permission_classes = [IsStudent, HasPermission]
    required_permission = "courses:read:enrolled"

    @extend_schema(
        tags=[TAG_LIVE],
        summary="Live sessions visible to the signed-in student with join flags",
        parameters=[
            OpenApiParameter(
                "scope",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                enum=["upcoming", "past", "all"],
                description="Default upcoming",
            )
        ],
        responses=StudentLiveSessionSerializer(many=True),
    )
    def get(self, request):
        scope = (request.query_params.get("scope") or "upcoming").lower()
        if scope not in ("upcoming", "past", "all"):
            raise ValidationFailed("scope must be upcoming, past or all.", details={"scope": ["Invalid scope."]})
        now = timezone.now()
        rows = selectors.filter_sessions_by_scope(selectors.sessions_visible_to(request.user), scope, now)
        return Response(StudentLiveSessionSerializer(rows, many=True, context={"now": now, "visible": True}).data)


class JoinLiveSessionView(APIView):
    """POST /student/live-sessions/{id}/join"""

    permission_classes = [IsStudent, HasPermission]
    required_permission = "live:join"

    @extend_schema(
        tags=[TAG_LIVE],
        summary="Mint a short-lived signed join URL",
        request=None,
        responses=JoinSessionResponseSerializer,
    )
    def post(self, request, session_id: str):
        data = services.join_session(request.user, session_id)
        return Response(JoinSessionResponseSerializer(data).data)


class StudentRecordingsView(APIView):
    """GET /student/recordings?courseId&from&to"""

    permission_classes = [IsStudent, HasPermission]
    required_permission = "recordings:watch"

    @extend_schema(
        tags=[TAG_RECORDINGS],
        summary="Recordings of sessions visible to the student (all statuses)",
        parameters=[
            OpenApiParameter("courseId", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter(
                "from", OpenApiTypes.DATE, OpenApiParameter.QUERY, description="Recorded on/after (date or datetime)"
            ),
            OpenApiParameter(
                "to", OpenApiTypes.DATE, OpenApiParameter.QUERY, description="Recorded on/before (date or datetime)"
            ),
        ],
        responses=RecordingSerializer(many=True),
    )
    def get(self, request):
        qs = selectors.recordings_visible_to(request.user)
        course_id = query_param(request, "courseId")
        if course_id:
            qs = qs.filter(course_id=course_id)
        start = _parse_datetime_param(request, "from")
        end = _parse_datetime_param(request, "to", end_of_day=True)
        if start:
            qs = qs.filter(recorded_at__gte=start)
        if end:
            qs = qs.filter(recorded_at__lt=end)
        return Response(RecordingSerializer(qs.order_by("-recorded_at"), many=True).data)


class RecordingPlaybackView(APIView):
    """POST /student/recordings/{id}/playback"""

    permission_classes = [IsStudent, HasPermission]
    required_permission = "recordings:watch"

    @extend_schema(
        tags=[TAG_RECORDINGS],
        summary="Authorize playback and return a signed, expiring stream URL",
        request=None,
        responses=RecordingPlaybackResponseSerializer,
    )
    def post(self, request, recording_id: str):
        data = services.playback(request.user, recording_id)
        return Response(RecordingPlaybackResponseSerializer(data).data)


class RecordingMediaView(APIView):
    """
    GET /media/recordings/{token}

    Streams the recording file referenced by a signed, expiring token. The token
    (not an Authorization header) is the credential because <video src> cannot send
    headers. Development serves MEDIA_ROOT/<storage_key> via FileResponse; in
    production this view should instead 302-redirect to a CDN/object-storage signed
    URL (S3 presigned GET, CloudFront signed URL) so media bytes never pass through
    the application server.
    """

    permission_classes = [AllowAny]
    authentication_classes = []

    @extend_schema(
        tags=[TAG_RECORDINGS],
        summary="Stream a recording using a signed token",
        responses={
            (200, "video/*"): OpenApiTypes.BINARY,
            400: OpenApiTypes.OBJECT,
            404: OpenApiTypes.OBJECT,
            410: OpenApiTypes.OBJECT,
        },
    )
    def get(self, request, token: str):
        try:
            payload = services.read_recording_token(token)
        except signing.SignatureExpired:
            raise ApiError(
                "This stream link has expired. Request playback again.",
                code="TOKEN_EXPIRED",
                status_code=status.HTTP_410_GONE,
            )
        except signing.BadSignature:
            raise ApiError("Invalid stream token.", code="INVALID_TOKEN", status_code=status.HTTP_400_BAD_REQUEST)
        recording = Recording.objects.filter(pk=payload.get("rid"), status="available").first()
        if recording is None or not recording.storage_key:
            raise NotFoundError("Recording not found.")
        root = Path(settings.MEDIA_ROOT).resolve()
        path = (root / recording.storage_key).resolve()
        if root not in path.parents or not path.is_file():
            raise NotFoundError("Recording file not found.")
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        response = FileResponse(path.open("rb"), content_type=content_type)
        response["Cache-Control"] = "private, no-store"
        return response


# ---------------------------------------------------------------------------
# Admin: batches
# ---------------------------------------------------------------------------


class AdminBatchListCreateView(ListAPIView):
    """GET /admin/batches (paginated BatchSummary) and POST /admin/batches."""

    permission_classes = [HasPermission]
    required_permission = "batches:manage"
    serializer_class = BatchSummarySerializer
    pagination_class = PageNumberWithSizePagination
    filter_backends = []

    def get_queryset(self):
        request = self.request
        status_value = query_param(request, "status")
        if status_value and status_value not in dict(Batch.STATUS_CHOICES):
            raise ValidationFailed("Unknown batch status.", details={"status": ["Invalid status."]})
        return selectors.filter_batches(
            selectors.batch_summary_queryset(),
            search=query_param(request, "search"),
            course_id=query_param(request, "courseId"),
            trainer_id=query_param(request, "trainerId"),
            status=status_value,
            start_from=_parse_date_param(request, "startFrom"),
            start_to=_parse_date_param(request, "startTo"),
        )

    @extend_schema(
        tags=[TAG_BATCHES],
        summary="List batches (all statuses) with membership counters",
        parameters=[
            OpenApiParameter("search", OpenApiTypes.STR, OpenApiParameter.QUERY, description="Name, code or id"),
            OpenApiParameter("courseId", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("trainerId", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter(
                "status", OpenApiTypes.STR, OpenApiParameter.QUERY, enum=[c[0] for c in Batch.STATUS_CHOICES] + ["all"]
            ),
            OpenApiParameter("startFrom", OpenApiTypes.DATE, OpenApiParameter.QUERY),
            OpenApiParameter("startTo", OpenApiTypes.DATE, OpenApiParameter.QUERY),
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)

    @extend_schema(
        tags=[TAG_BATCHES],
        summary="Create a batch (status scheduled)",
        request=BatchUpsertSerializer,
        responses={201: BatchSerializer},
    )
    def post(self, request, *args, **kwargs):
        serializer = BatchUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        batch = services.create_batch(serializer.validated_data, actor=request.user, request=request)
        return Response(BatchSerializer(batch).data, status=status.HTTP_201_CREATED)


class AdminBatchDetailView(APIView):
    """GET /admin/batches/{id} and PUT /admin/batches/{id}."""

    permission_classes = [HasPermission]
    required_permission = "batches:manage"

    @extend_schema(
        tags=[TAG_BATCHES],
        summary="Batch detail with students, history and session ids",
        responses=BatchDetailSerializer,
    )
    def get(self, request, batch_id: str):
        batch = get_object_or_404(selectors.batch_summary_queryset(), pk=batch_id)
        context = {
            "students": selectors.batch_student_rows(batch),
            "history": batch.history.select_related("actor").order_by("-at", "-id"),
            "session_ids": list(
                LiveSession.objects.filter(batch=batch).order_by("starts_at").values_list("id", flat=True)
            ),
        }
        return Response(BatchDetailSerializer(batch, context=context).data)

    @extend_schema(
        tags=[TAG_BATCHES],
        summary="Update batch details (409 ARCHIVED when read-only)",
        request=BatchUpsertSerializer,
        responses=BatchSerializer,
    )
    def put(self, request, batch_id: str):
        batch = get_object_or_404(Batch, pk=batch_id)
        serializer = BatchUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        batch = services.update_batch(batch, serializer.validated_data, actor=request.user, request=request)
        return Response(BatchSerializer(batch).data)


class AdminBatchStatusView(APIView):
    """POST /admin/batches/{id}/status"""

    permission_classes = [HasPermission]
    required_permission = "batches:manage"

    @extend_schema(
        tags=[TAG_BATCHES],
        summary="Change batch status (enforces transitions)",
        request=BatchStatusChangeSerializer,
        responses=BatchSerializer,
    )
    def post(self, request, batch_id: str):
        batch = get_object_or_404(Batch, pk=batch_id)
        serializer = BatchStatusChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        batch = services.change_batch_status(
            batch,
            serializer.validated_data["status"],
            reason=serializer.validated_data.get("reason", ""),
            actor=request.user,
            request=request,
        )
        return Response(BatchSerializer(batch).data)


class AdminBatchStudentsView(APIView):
    """POST /admin/batches/{id}/students"""

    permission_classes = [HasPermission]
    required_permission = "batches:manage"

    @extend_schema(
        tags=[TAG_BATCHES],
        summary="Add a student to a batch",
        request=AddBatchStudentSerializer,
        responses={201: BatchEnrollmentSerializer},
    )
    def post(self, request, batch_id: str):
        get_object_or_404(Batch, pk=batch_id)
        serializer = AddBatchStudentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student = student_or_none(serializer.validated_data["student_id"])
        if student is None:
            raise NotFoundError("Student not found.")
        membership = services.add_student_to_batch(batch_id, student, actor=request.user, request=request)
        return Response(BatchEnrollmentSerializer(membership).data, status=status.HTTP_201_CREATED)


class AdminBatchWithdrawStudentView(APIView):
    """POST /admin/batches/{id}/students/{studentId}/withdraw"""

    permission_classes = [HasPermission]
    required_permission = "batches:manage"

    @extend_schema(
        tags=[TAG_BATCHES],
        summary="Withdraw a student from a batch",
        request=WithdrawBatchStudentSerializer,
        responses=BatchEnrollmentSerializer,
    )
    def post(self, request, batch_id: str, student_id: str):
        batch = get_object_or_404(Batch.objects.select_related("course"), pk=batch_id)
        serializer = WithdrawBatchStudentSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        membership = services.withdraw_student_from_batch(
            batch, student_id, reason=serializer.validated_data.get("reason", ""), actor=request.user, request=request
        )
        return Response(BatchEnrollmentSerializer(membership).data)


# ---------------------------------------------------------------------------
# Admin: live sessions
# ---------------------------------------------------------------------------


class AdminLiveSessionListCreateView(GenericAPIView):
    """GET /admin/live-sessions?batchId&courseId&scope and POST /admin/live-sessions."""

    permission_classes = [HasPermission]
    required_permission = "batches:manage"
    serializer_class = LiveSessionSerializer
    filter_backends = []
    pagination_class = None

    @extend_schema(
        tags=[TAG_LIVE],
        summary="List live sessions (admin)",
        parameters=[
            OpenApiParameter("batchId", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("courseId", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter(
                "scope",
                OpenApiTypes.STR,
                OpenApiParameter.QUERY,
                enum=["all", "upcoming"],
                description="upcoming = scheduled and starts in the future",
            ),
        ],
        responses=LiveSessionSerializer(many=True),
    )
    def get(self, request):
        qs = selectors.session_queryset().order_by("starts_at")
        batch_id = query_param(request, "batchId")
        course_id = query_param(request, "courseId")
        if batch_id:
            qs = qs.filter(batch_id=batch_id)
        if course_id:
            qs = qs.filter(course_id=course_id)
        if (request.query_params.get("scope") or "all") == "upcoming":
            qs = qs.filter(status="scheduled", starts_at__gt=timezone.now())
        return Response(LiveSessionSerializer(qs, many=True).data)

    @extend_schema(
        tags=[TAG_LIVE],
        summary="Schedule a live session",
        request=LiveSessionUpsertSerializer,
        responses={201: LiveSessionSerializer},
    )
    def post(self, request):
        serializer = LiveSessionUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        session = services.create_session(serializer.validated_data, actor=request.user, request=request)
        session = selectors.session_queryset().get(pk=session.pk)
        return Response(LiveSessionSerializer(session).data, status=status.HTTP_201_CREATED)


class AdminLiveSessionCancelView(APIView):
    """POST /admin/live-sessions/{id}/cancel"""

    permission_classes = [HasPermission]
    required_permission = "batches:manage"

    @extend_schema(
        tags=[TAG_LIVE],
        summary="Cancel a live session (409 ALREADY_COMPLETED)",
        request=None,
        responses=LiveSessionSerializer,
    )
    def post(self, request, session_id: str):
        session = get_object_or_404(selectors.session_queryset().select_related("batch"), pk=session_id)
        session = services.cancel_session(session, actor=request.user, request=request)
        return Response(LiveSessionSerializer(session).data)


class AdminLiveSessionAttendanceView(APIView):
    """POST /admin/live-sessions/{id}/attendance"""

    permission_classes = [HasPermission]
    required_permission = "batches:manage"

    @extend_schema(
        tags=[TAG_LIVE],
        summary="Record attendance, complete the session and evaluate completion",
        request=AttendanceRequestSerializer,
        responses=AttendanceResponseSerializer,
    )
    def post(self, request, session_id: str):
        session = get_object_or_404(LiveSession.objects.select_related("course", "batch", "lesson"), pk=session_id)
        serializer = AttendanceRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.record_attendance(
            session, serializer.validated_data["records"], actor=request.user, request=request
        )
        return Response(AttendanceResponseSerializer(result).data)


class AdminLiveSessionRecordingView(APIView):
    """POST /admin/live-sessions/{id}/recording"""

    permission_classes = [HasPermission]
    required_permission = "batches:manage"

    @extend_schema(
        tags=[TAG_RECORDINGS],
        summary="Create or update the session recording (mark available/processing/unavailable)",
        request=RecordingUpsertSerializer,
        responses=RecordingSerializer,
    )
    def post(self, request, session_id: str):
        session = get_object_or_404(LiveSession.objects.select_related("course", "trainer"), pk=session_id)
        existing = Recording.objects.filter(session=session).first()
        serializer = RecordingUpsertSerializer(existing, data=request.data)
        serializer.is_valid(raise_exception=True)
        recording = services.upsert_recording(session, serializer.validated_data, actor=request.user, request=request)
        recording = selectors.recording_queryset().get(pk=recording.pk)
        return Response(
            RecordingSerializer(recording).data, status=status.HTTP_200_OK if existing else status.HTTP_201_CREATED
        )
