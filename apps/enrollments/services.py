"""
Enrollment, progress and completion services (CONTRACT).

These functions are the shared business-rule entry points other apps call.
Signatures are fixed; implementations are provided by the enrollments app.

Rules (see docs/API_CONTRACTS.md, "Completion and certificate rules"):
- Access exists only when a CourseEnrollment with access_status='active' exists.
- compute_progress() derives everything from LessonProgress + AttendanceRecord; the
  client never sends completion state for a course.
- evaluate_completion() sets CourseEnrollment.completed_at when eligible, marks the
  BatchEnrollment completed (+ history event) and, when the course is
  certificate_enabled with certificate_issuance='automatic', calls
  apps.certificates.services.issue_certificate(...).
- grant_access_for_payment() is called inside the payment webhook transaction and
  must be idempotent (one enrollment per student+course).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from django.db import transaction
from django.utils import timezone

from apps.batches.models import AttendanceRecord, BatchEnrollment, BatchHistoryEvent, LiveSession
from apps.courses.models import Lesson
from apps.enrollments.models import CourseEnrollment, LessonProgress
from common.exceptions import ForbiddenError

ATTENDED_STATUSES = ("present", "late")


@dataclass
class ProgressSummary:
    course_id: str
    completed_lessons: int
    total_lessons: int
    required_lessons: int
    completed_required_lessons: int
    progress_percent: int
    last_lesson_id: str | None
    lessons: list  # list[LessonProgress]
    live_attendance_percent: int | None
    assessment_passed: bool | None
    completion_eligible: bool
    is_completed: bool
    completed_at: object | None
    extra: dict = field(default_factory=dict)


@dataclass
class ProgressInputs:
    """Everything compute_progress needs, so callers can bulk-load it for many courses."""

    lessons: list  # list[Lesson] for the course
    progress_rows: list  # list[LessonProgress] for (student, course)
    enrollment: CourseEnrollment | None
    completed_sessions: list  # list[LiveSession] with status='completed' for the course (all batches)
    attended_session_ids: set  # ids of sessions where the student was present/late


def percent(numerator: int, denominator: int) -> int:
    """Integer percentage rounded half-up (matches JavaScript's Math.round in the mock)."""
    if not denominator:
        return 0
    return (200 * numerator + denominator) // (2 * denominator)


def course_lessons(course) -> list:
    """All lessons of a course in curriculum order, using prefetched modules when available."""
    cache = getattr(course, "_prefetched_objects_cache", {})
    if "modules" in cache:
        return [lesson for module in course.modules.all() for lesson in module.lessons.all()]
    return list(Lesson.objects.filter(module__course=course).order_by("module__order", "order"))


def attendance_percent(enrollment, completed_sessions, attended_session_ids) -> int | None:
    """Attendance over completed sessions (batch-scoped when the enrollment has a batch); None if none completed."""
    batch_id = enrollment.batch_id if enrollment is not None else None
    sessions = [s for s in completed_sessions if (s.batch_id == batch_id if batch_id else True)]
    if not sessions:
        return None
    present = sum(1 for s in sessions if s.id in attended_session_ids)
    return percent(present, len(sessions))


def summarize_progress(course, inputs: ProgressInputs) -> ProgressSummary:
    lessons = inputs.lessons
    progress_rows = sorted(inputs.progress_rows, key=lambda p: p.updated_at, reverse=True)
    completed_ids = {p.lesson_id for p in progress_rows if p.completed}
    required = [lesson for lesson in lessons if lesson.is_required]
    completed_required = sum(1 for lesson in required if lesson.id in completed_ids)
    completed_lessons = sum(1 for lesson in lessons if lesson.id in completed_ids)
    enrollment = inputs.enrollment

    att_pct = attendance_percent(enrollment, inputs.completed_sessions, inputs.attended_session_ids)
    assessment_lessons = [lesson for lesson in lessons if lesson.type == "assessment"]
    if course.require_assessment:
        assessment_passed = (
            None if not assessment_lessons else all(lesson.id in completed_ids for lesson in assessment_lessons)
        )
    else:
        assessment_passed = None

    eligible = enrollment is not None and enrollment.access_status == "active"
    if eligible:
        if course.require_all_required_lessons and completed_required < len(required):
            eligible = False
        if course.min_live_attendance_percent is not None:
            # Attendance can only be evaluated once live sessions have occurred.
            if att_pct is None or att_pct < course.min_live_attendance_percent:
                eligible = False
        if course.require_assessment and assessment_passed is not True:
            eligible = False

    return ProgressSummary(
        course_id=course.id,
        completed_lessons=completed_lessons,
        total_lessons=len(lessons),
        required_lessons=len(required),
        completed_required_lessons=completed_required,
        progress_percent=percent(completed_lessons, len(lessons)),
        last_lesson_id=progress_rows[0].lesson_id if progress_rows else None,
        lessons=progress_rows,
        live_attendance_percent=att_pct,
        assessment_passed=assessment_passed,
        completion_eligible=eligible,
        is_completed=bool(enrollment and enrollment.completed_at),
        completed_at=enrollment.completed_at if enrollment else None,
    )


def load_progress_inputs(student, course, enrollment: CourseEnrollment | None = None) -> ProgressInputs:
    if enrollment is None:
        enrollment = CourseEnrollment.objects.filter(student=student, course=course).first()
    completed_sessions = list(LiveSession.objects.filter(course=course, status="completed").only("id", "batch_id"))
    attended = set(
        AttendanceRecord.objects.filter(
            session__in=[s.id for s in completed_sessions], student=student, status__in=ATTENDED_STATUSES
        ).values_list("session_id", flat=True)
    )
    return ProgressInputs(
        lessons=course_lessons(course),
        progress_rows=list(LessonProgress.objects.filter(student=student, course=course)),
        enrollment=enrollment,
        completed_sessions=completed_sessions,
        attended_session_ids=attended,
    )


def get_active_enrollment(student, course_id: str):
    """Return the active CourseEnrollment for (student, course) or None."""
    return CourseEnrollment.objects.filter(student=student, course_id=course_id, access_status="active").first()


def is_entitled(student, course_id: str) -> bool:
    """True when the student has active access to the course."""
    return CourseEnrollment.objects.filter(student=student, course_id=course_id, access_status="active").exists()


def compute_progress(student, course) -> ProgressSummary:
    """Progress + eligibility for a student in a course (server-side truth)."""
    return summarize_progress(course, load_progress_inputs(student, course))


def update_lesson_progress(
    student, course, lesson, *, completed: bool | None, last_position_seconds: int | None
) -> ProgressSummary:
    """Upsert LessonProgress (live lessons cannot be completed manually), then evaluate completion."""
    if completed is True and lesson.type == "live":
        raise ForbiddenError("Live lessons are marked complete through recorded attendance.", code="NOT_PERMITTED")
    now = timezone.now()
    with transaction.atomic():
        record, _ = LessonProgress.objects.select_for_update().get_or_create(
            student=student, lesson=lesson, defaults={"course": course}
        )
        if last_position_seconds is not None:
            record.last_position_seconds = max(0, int(last_position_seconds))
        if completed is not None:
            record.completed = completed
            record.completed_at = now if completed else None
        record.save()
    evaluate_completion(student, course)
    return compute_progress(student, course)


def evaluate_completion(student, course) -> bool:
    """Mark the enrollment complete when criteria are met; returns True if it became complete now."""
    with transaction.atomic():
        enrollment = (
            CourseEnrollment.objects.select_for_update()
            .filter(student=student, course=course)
            .select_related("batch")
            .first()
        )
        if enrollment is None or enrollment.access_status != "active" or enrollment.completed_at:
            return False
        summary = summarize_progress(course, load_progress_inputs(student, course, enrollment))
        if not summary.completion_eligible:
            return False
        now = timezone.now()
        enrollment.completed_at = now
        enrollment.save(update_fields=["completed_at", "updated_at"])

        if enrollment.batch_id:
            membership = (
                BatchEnrollment.objects.select_for_update()
                .filter(batch_id=enrollment.batch_id, student=student)
                .exclude(status="withdrawn")
                .first()
            )
            if membership is not None:
                membership.status = "completed"
                membership.completed_at = now
                membership.save(update_fields=["status", "completed_at", "updated_at"])
                BatchHistoryEvent.objects.create(
                    batch_id=enrollment.batch_id,
                    type="student_completed",
                    description=f"{student.name} completed the batch requirements.",
                    actor=None,
                    actor_name="System",
                )

        if course.certificate_enabled and course.certificate_issuance == "automatic":
            from apps.certificates import services as certificate_services

            certificate_services.issue_certificate(student, course, enrollment.batch, trusted=True)
        return True


def grant_access_for_payment(payment):
    """Idempotently create/activate the CourseEnrollment for a paid PaymentTransaction. Returns the enrollment."""
    enrollment, created = CourseEnrollment.objects.get_or_create(
        student_id=payment.student_id,
        course_id=payment.course_id,
        defaults={"source": "purchase", "access_status": "active", "payment": payment},
    )
    if not created and (enrollment.access_status != "active" or enrollment.payment_id != payment.id):
        enrollment.access_status = "active"
        enrollment.payment = payment
        enrollment.save(update_fields=["access_status", "payment", "updated_at"])
    return enrollment


def revoke_access_for_refund(payment, *, actor=None):
    """Set the enrollment to revoked and withdraw the student from the batch (history event)."""
    now = timezone.now()
    enrollments = list(CourseEnrollment.objects.select_for_update().filter(payment=payment).select_related("student"))
    for enrollment in enrollments:
        enrollment.access_status = "revoked"
        enrollment.save(update_fields=["access_status", "updated_at"])
        if not enrollment.batch_id:
            continue
        membership = (
            BatchEnrollment.objects.select_for_update()
            .filter(batch_id=enrollment.batch_id, student_id=enrollment.student_id)
            .exclude(status="withdrawn")
            .first()
        )
        if membership is None:
            continue
        membership.status = "withdrawn"
        membership.withdrawn_at = now
        membership.save(update_fields=["status", "withdrawn_at", "updated_at"])
        BatchHistoryEvent.objects.create(
            batch_id=enrollment.batch_id,
            type="student_withdrawn",
            description=f"{enrollment.student.name} withdrawn after refund.",
            actor=actor if getattr(actor, "pk", None) else None,
            actor_name=getattr(actor, "name", None) or "System",
        )
    return enrollments
