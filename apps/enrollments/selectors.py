"""
Query helpers that assemble the student-facing views (My Courses, dashboard) with a
fixed number of queries regardless of how many courses the student is enrolled in.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Prefetch
from django.utils import timezone

from apps.batches.models import AttendanceRecord, BatchEnrollment, LiveSession
from apps.certificates.models import Certificate
from apps.courses.models import Course, CourseModule
from apps.enrollments.models import CourseEnrollment, LessonProgress
from apps.enrollments.services import ATTENDED_STATUSES, ProgressInputs, course_lessons, summarize_progress


def course_with_curriculum_queryset():
    """Course queryset with trainer, modules, lessons and materials prefetched (student course page)."""
    return Course.objects.select_related("trainer").prefetch_related(
        Prefetch("modules", queryset=CourseModule.objects.order_by("order").prefetch_related("lessons__materials"))
    )


def student_enrollments_queryset(student):
    return (
        CourseEnrollment.objects.filter(student=student)
        .exclude(access_status="pending_payment")
        .select_related("course__trainer", "batch")
        .prefetch_related("course__modules__lessons")
        .order_by("-enrolled_at")
    )


def visible_sessions_queryset(student, course_ids, member_batch_ids):
    """Sessions the student may see: course entitled and (no batch or member of that batch, not withdrawn)."""
    from django.db.models import Q

    return LiveSession.objects.filter(course_id__in=course_ids).filter(
        Q(batch__isnull=True) | Q(batch_id__in=member_batch_ids)
    )


def join_window(session):
    opens_at = session.starts_at - timedelta(minutes=session.join_window_minutes_before)
    closes_at = session.starts_at + timedelta(minutes=session.duration_minutes)
    return opens_at, closes_at


def student_session_view(session, *, enrolled: bool, now) -> dict:
    """StudentLiveSession shape (snake_case; the renderer camelises)."""
    opens_at, closes_at = join_window(session)
    in_window = opens_at <= now <= closes_at
    recording = getattr(session, "recording", None)
    return {
        "id": session.id,
        "course_id": session.course_id,
        "course_title": session.course.title,
        "batch_id": session.batch_id,
        "title": session.title,
        "trainer_id": session.trainer_id,
        "trainer_name": session.trainer.name,
        "starts_at": session.starts_at,
        "duration_minutes": session.duration_minutes,
        "status": session.status,
        "join_window_minutes_before": session.join_window_minutes_before,
        "recording_id": recording.id if recording is not None else None,
        "description": session.description,
        "is_enrolled": enrolled,
        "can_join_now": enrolled and in_window and session.status in ("scheduled", "live"),
        "join_opens_at": opens_at,
        "join_closes_at": closes_at,
    }


@dataclass
class StudentLearningData:
    enrollments: list
    views: list  # EnrolledCourseView dicts, same order as `enrollments`
    upcoming_sessions: list  # scheduled, visible, not yet ended; ordered by starts_at
    certificates: list  # issued certificates, newest first
    now: object


def enrolled_course_view(enrollment, summary, next_session_at, certificate) -> dict:
    course = enrollment.course
    return {
        "enrollment": enrollment,
        "course_id": course.id,
        "title": course.title,
        "thumbnail_url": course.thumbnail_url,
        "trainer_name": course.trainer.name if course.trainer else "",
        "format": course.format,
        "progress_percent": summary.progress_percent,
        "completed_lessons": summary.completed_lessons,
        "total_lessons": summary.total_lessons,
        "next_session_at": next_session_at,
        "is_completed": bool(enrollment.completed_at),
        "certificate_id": certificate.id if certificate is not None else None,
    }


def load_student_learning_data(student) -> StudentLearningData:
    now = timezone.now()
    enrollments = list(student_enrollments_queryset(student))
    course_ids = [e.course_id for e in enrollments]
    active_course_ids = {e.course_id for e in enrollments if e.access_status == "active"}

    progress_by_course: dict[str, list] = defaultdict(list)
    for row in LessonProgress.objects.filter(student=student, course_id__in=course_ids):
        progress_by_course[row.course_id].append(row)

    completed_by_course: dict[str, list] = defaultdict(list)
    completed_sessions = list(
        LiveSession.objects.filter(course_id__in=course_ids, status="completed").only("id", "course_id", "batch_id")
    )
    for session in completed_sessions:
        completed_by_course[session.course_id].append(session)
    attended = set(
        AttendanceRecord.objects.filter(
            session_id__in=[s.id for s in completed_sessions], student=student, status__in=ATTENDED_STATUSES
        ).values_list("session_id", flat=True)
    )

    member_batch_ids = set(
        BatchEnrollment.objects.filter(student=student).exclude(status="withdrawn").values_list("batch_id", flat=True)
    )
    scheduled = list(
        visible_sessions_queryset(student, active_course_ids, member_batch_ids)
        .filter(status="scheduled")
        .select_related("course", "trainer", "recording")
        .order_by("starts_at")
    )
    # Mock rule: a scheduled session counts as "next" until it has fully ended.
    not_ended = [s for s in scheduled if s.starts_at + timedelta(minutes=s.duration_minutes) > now]
    next_by_course: dict[str, object] = {}
    for session in not_ended:
        next_by_course.setdefault(session.course_id, session.starts_at)

    certificates = list(Certificate.objects.filter(student=student, status="issued").order_by("-issued_at"))
    cert_by_course = {}
    for cert in certificates:
        cert_by_course.setdefault(cert.course_id, cert)

    views = []
    for enrollment in enrollments:
        course = enrollment.course
        inputs = ProgressInputs(
            lessons=course_lessons(course),
            progress_rows=progress_by_course.get(course.id, []),
            enrollment=enrollment,
            completed_sessions=completed_by_course.get(course.id, []),
            attended_session_ids=attended,
        )
        summary = summarize_progress(course, inputs)
        views.append(
            enrolled_course_view(enrollment, summary, next_by_course.get(course.id), cert_by_course.get(course.id))
        )

    return StudentLearningData(
        enrollments=enrollments, views=views, upcoming_sessions=not_ended, certificates=certificates, now=now
    )


def build_dashboard(student) -> dict:
    data = load_student_learning_data(student)
    views = data.views
    active = [v for v in views if v["enrollment"].access_status == "active" and not v["is_completed"]]
    started = sorted(
        (v for v in active if v["progress_percent"] > 0), key=lambda v: v["progress_percent"], reverse=True
    )
    continue_learning = started[0] if started else (active[0] if active else None)
    return {
        "stats": {
            "total_enrolled": len(views),
            "active": len(active),
            "upcoming_live_classes": len(data.upcoming_sessions),
            "completed": sum(1 for v in views if v["is_completed"]),
            "certificates": len(data.certificates),
        },
        "continue_learning": continue_learning,
        "upcoming_sessions": [student_session_view(s, enrolled=True, now=data.now) for s in data.upcoming_sessions[:5]],
        "recent_courses": views[:4],
        "recent_certificates": data.certificates[:3],
    }
