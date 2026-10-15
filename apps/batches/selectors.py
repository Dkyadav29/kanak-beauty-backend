"""Reusable querysets and read-only computations for batches, live sessions and recordings."""

from __future__ import annotations

from datetime import date, datetime, timedelta

from django.db.models import Count, Exists, OuterRef, Q, QuerySet
from django.utils import timezone

from apps.batches.models import AttendanceRecord, Batch, BatchEnrollment, LiveSession, Recording
from apps.certificates.models import Certificate
from apps.courses.models import Lesson
from apps.enrollments.models import CourseEnrollment, LessonProgress

ACTIVE_MEMBERSHIP_STATUSES = ("enrolled", "in_progress", "completed")
PUBLIC_UPCOMING_DAYS = 45

# ---------------------------------------------------------------------------
# Live sessions
# ---------------------------------------------------------------------------


def session_queryset() -> QuerySet[LiveSession]:
    """Base queryset with everything the LiveSession serializer needs (no N+1)."""
    return LiveSession.objects.select_related("course", "trainer", "recording")


def public_upcoming_sessions(limit: int = 12) -> QuerySet[LiveSession]:
    """Scheduled sessions of published courses in the next 45 days (also includes the last hour)."""
    now = timezone.now()
    return (
        session_queryset()
        .filter(
            status="scheduled",
            course__status="published",
            starts_at__gt=now - timedelta(hours=1),
            starts_at__lt=now + timedelta(days=PUBLIC_UPCOMING_DAYS),
        )
        .order_by("starts_at")[: max(1, min(int(limit), 100))]
    )


def has_active_enrollment(student, course_id: str) -> bool:
    return CourseEnrollment.objects.filter(student=student, course_id=course_id, access_status="active").exists()


def sessions_visible_to(student) -> QuerySet[LiveSession]:
    """
    Sessions the student may see: an ACTIVE CourseEnrollment for the session's
    course AND (the session is course-wide OR the student is a non-withdrawn
    member of the session's batch).
    """
    active_enrollment = CourseEnrollment.objects.filter(
        student=student, course_id=OuterRef("course_id"), access_status="active"
    )
    membership = BatchEnrollment.objects.filter(student=student, batch_id=OuterRef("batch_id")).exclude(
        status="withdrawn"
    )
    return (
        session_queryset()
        .annotate(_entitled=Exists(active_enrollment), _member=Exists(membership))
        .filter(_entitled=True)
        .filter(Q(batch__isnull=True) | Q(_member=True))
    )


def join_window(session: LiveSession) -> tuple[datetime, datetime]:
    opens_at = session.starts_at - timedelta(minutes=session.join_window_minutes_before)
    closes_at = session.starts_at + timedelta(minutes=session.duration_minutes)
    return opens_at, closes_at


def session_ends_at(session: LiveSession) -> datetime:
    return session.starts_at + timedelta(minutes=session.duration_minutes)


def can_join_now(session: LiveSession, *, visible: bool, now: datetime | None = None) -> bool:
    now = now or timezone.now()
    opens_at, closes_at = join_window(session)
    return visible and opens_at <= now <= closes_at and session.status in ("scheduled", "live")


def filter_sessions_by_scope(sessions, scope: str, now: datetime | None = None) -> list[LiveSession]:
    """
    Mirrors the frontend mock: ``upcoming`` = not yet ended and not completed,
    ``past`` = ended or completed (newest first), ``all`` = everything (oldest first).
    """
    now = now or timezone.now()
    rows = list(sessions)
    if scope == "upcoming":
        rows = [s for s in rows if session_ends_at(s) > now and s.status != "completed"]
        rows.sort(key=lambda s: s.starts_at)
    elif scope == "past":
        rows = [s for s in rows if session_ends_at(s) <= now or s.status == "completed"]
        rows.sort(key=lambda s: s.starts_at, reverse=True)
    else:
        rows.sort(key=lambda s: s.starts_at)
    return rows


# ---------------------------------------------------------------------------
# Recordings
# ---------------------------------------------------------------------------


def recording_queryset() -> QuerySet[Recording]:
    return Recording.objects.select_related("course", "lesson", "session", "session__trainer")


def recordings_visible_to(student) -> QuerySet[Recording]:
    """Recordings of sessions visible to the student (any status, so the UI can label them)."""
    return recording_queryset().filter(session__in=sessions_visible_to(student).values("pk"))


# ---------------------------------------------------------------------------
# Progress / attendance (read-only helpers; the enrollments app owns the contract)
# ---------------------------------------------------------------------------


def progress_percent(student, course) -> int:
    total = Lesson.objects.filter(module__course=course).count()
    if total == 0:
        return 0
    completed = LessonProgress.objects.filter(student=student, lesson__module__course=course, completed=True).count()
    return round(completed / total * 100)


def attendance_percent(student, course, batch=None) -> int | None:
    """present+late over completed sessions of the course (batch-scoped when a batch is given)."""
    sessions = LiveSession.objects.filter(course=course, status="completed")
    if batch is not None:
        sessions = sessions.filter(batch=batch)
    total = sessions.count()
    if total == 0:
        return None
    present = AttendanceRecord.objects.filter(
        session__in=sessions, student=student, status__in=("present", "late")
    ).count()
    return round(present / total * 100)


# ---------------------------------------------------------------------------
# Batches (admin)
# ---------------------------------------------------------------------------


def batch_summary_queryset() -> QuerySet[Batch]:
    """Batches annotated with the BatchSummary counters in a single query."""
    return (
        Batch.objects.select_related("course", "trainer")
        .annotate(
            total_students=Count("memberships", filter=~Q(memberships__status="withdrawn"), distinct=True),
            completed_students=Count("memberships", filter=Q(memberships__status="completed"), distinct=True),
            in_progress_students=Count(
                "memberships", filter=Q(memberships__status__in=("enrolled", "in_progress")), distinct=True
            ),
            withdrawn_students=Count("memberships", filter=Q(memberships__status="withdrawn"), distinct=True),
            session_count=Count("sessions", distinct=True),
        )
        .order_by("-start_date", "-created_at")
    )


def filter_batches(
    qs: QuerySet[Batch],
    *,
    search=None,
    course_id=None,
    trainer_id=None,
    status=None,
    start_from: date | None = None,
    start_to: date | None = None,
):
    if search:
        term = search.strip()
        qs = qs.filter(Q(name__icontains=term) | Q(code__icontains=term) | Q(id__icontains=term))
    if course_id:
        qs = qs.filter(course_id=course_id)
    if trainer_id:
        qs = qs.filter(trainer_id=trainer_id)
    if status:
        qs = qs.filter(status=status)
    if start_from:
        qs = qs.filter(start_date__gte=start_from)
    if start_to:
        qs = qs.filter(start_date__lte=start_to)
    return qs


def batch_student_rows(batch: Batch) -> list[dict]:
    """
    BatchStudentRow data for every membership of the batch (including withdrawn),
    computed with a fixed number of queries regardless of batch size.
    """
    memberships = list(
        BatchEnrollment.objects.filter(batch=batch).select_related("student").order_by("enrolled_at", "id")
    )
    if not memberships:
        return []
    student_ids = [m.student_id for m in memberships]
    course = batch.course

    total_lessons = Lesson.objects.filter(module__course=course).count()
    completed_by_student = dict(
        LessonProgress.objects.filter(student_id__in=student_ids, lesson__module__course=course, completed=True)
        .values_list("student_id")
        .annotate(n=Count("id"))
        .values_list("student_id", "n")
    )

    completed_sessions = LiveSession.objects.filter(course=course, batch=batch, status="completed")
    total_sessions = completed_sessions.count()
    present_by_student = dict(
        AttendanceRecord.objects.filter(
            session__in=completed_sessions, student_id__in=student_ids, status__in=("present", "late")
        )
        .values_list("student_id")
        .annotate(n=Count("id"))
        .values_list("student_id", "n")
    )

    certificate_by_student = dict(
        Certificate.objects.filter(course=course, student_id__in=student_ids, status="issued").values_list(
            "student_id", "certificate_number"
        )
    )

    rows = []
    for m in memberships:
        completed = completed_by_student.get(m.student_id, 0)
        present = present_by_student.get(m.student_id, 0)
        rows.append(
            {
                "enrollment": m,
                "student_id": m.student_id,
                "student_name": m.student.name,
                "student_email": m.student.email,
                "progress_percent": round(completed / total_lessons * 100) if total_lessons else 0,
                "attendance_percent": round(present / total_sessions * 100) if total_sessions else None,
                "certificate_number": certificate_by_student.get(m.student_id),
            }
        )
    return rows
