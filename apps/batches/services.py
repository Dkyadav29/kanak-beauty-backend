"""
Business rules for batches, live sessions, attendance and recordings.

Every admin mutation writes an AuditLog row (apps.core.services.log_audit) and,
when a batch is involved, a BatchHistoryEvent. Nothing is hard-deleted.
"""

from __future__ import annotations

import re

from django.core import signing
from django.db import transaction
from django.utils import timezone

from apps.batches.models import AttendanceRecord, Batch, BatchEnrollment, BatchHistoryEvent, LiveSession, Recording
from apps.batches.providers import get_provider, signed_url_ttl
from apps.batches.selectors import can_join_now, recordings_visible_to, sessions_visible_to
from apps.core.services import log_audit
from apps.courses.models import Lesson
from apps.enrollments import services as enrollment_services
from apps.enrollments.models import CourseEnrollment, LessonProgress
from common.exceptions import ConflictError, ForbiddenError, NotFoundError, ValidationFailed

RECORDING_TOKEN_SALT = "batches.recording-stream"
CLOSED_BATCH_STATUSES = ("completed", "cancelled", "archived")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def add_history(batch: Batch, event_type: str, description: str, actor=None) -> BatchHistoryEvent:
    return BatchHistoryEvent.objects.create(
        batch=batch,
        type=event_type,
        description=description[:400],
        actor=actor if getattr(actor, "pk", None) else None,
        actor_name=getattr(actor, "name", None) or "System",
    )


def _course_prefix(title: str) -> str:
    """Two uppercase letters derived from the course title (initials, padded from the title)."""
    words = [w for w in re.split(r"\s+", title.strip()) if w and w[0].isalpha()]
    initials = "".join(w[0] for w in words)[:2].upper()
    if len(initials) < 2:
        letters = "".join(ch for ch in title if ch.isalpha()).upper()
        initials = (initials + letters[len(initials) : 2]).ljust(2, "X")
    return initials


def generate_batch_code(course) -> str:
    prefix = _course_prefix(course.title)
    seq = Batch.objects.filter(course=course).count() + 1
    while True:
        code = f"KBA-{prefix}-{seq:02d}"
        if not Batch.objects.filter(code=code).exists():
            return code
        seq += 1


# ---------------------------------------------------------------------------
# Batches (admin)
# ---------------------------------------------------------------------------


@transaction.atomic
def create_batch(data: dict, *, actor, request=None) -> Batch:
    course = data["course"]
    batch = Batch.objects.create(
        code=generate_batch_code(course),
        name=data["name"],
        course=course,
        trainer=data["trainer"],
        start_date=data["start_date"],
        end_date=data["end_date"],
        capacity=data["capacity"],
        schedule_note=data.get("schedule_note", ""),
        status="scheduled",
    )
    add_history(batch, "batch_created", f"Batch {batch.code} created for {course.title}.", actor)
    log_audit(
        actor=actor,
        action="batch.create",
        entity_type="batch",
        entity_id=batch.id,
        details=f"Created batch {batch.code} ({batch.name}).",
        request=request,
    )
    return batch


@transaction.atomic
def update_batch(batch: Batch, data: dict, *, actor, request=None) -> Batch:
    if batch.status == "archived":
        raise ConflictError("Archived batches are read-only.", code="ARCHIVED")
    batch.name = data["name"]
    batch.course = data["course"]
    batch.trainer = data["trainer"]
    batch.start_date = data["start_date"]
    batch.end_date = data["end_date"]
    batch.capacity = data["capacity"]
    batch.schedule_note = data.get("schedule_note", "")
    batch.save()
    add_history(batch, "batch_updated", "Batch details updated.", actor)
    log_audit(
        actor=actor,
        action="batch.update",
        entity_type="batch",
        entity_id=batch.id,
        details=f"Updated batch {batch.code}.",
        request=request,
    )
    return batch


@transaction.atomic
def change_batch_status(batch: Batch, new_status: str, *, reason: str = "", actor, request=None) -> Batch:
    allowed = Batch.TRANSITIONS.get(batch.status, ())
    if new_status not in allowed:
        raise ConflictError(f"A {batch.status} batch cannot be moved to {new_status}.", code="INVALID_TRANSITION")
    previous = batch.status
    batch.status = new_status
    batch.save(update_fields=["status", "updated_at"])
    if new_status == "cancelled":
        LiveSession.objects.filter(batch=batch, status="scheduled").update(status="cancelled")
    suffix = f" Reason: {reason}" if reason else ""
    add_history(batch, "status_changed", f"Status changed from {previous} to {new_status}.{suffix}", actor)
    log_audit(
        actor=actor,
        action="batch.archive" if new_status == "archived" else "batch.status",
        entity_type="batch",
        entity_id=batch.id,
        details=f"Batch {batch.code}: {previous} -> {new_status}.{suffix}",
        request=request,
    )
    return batch


@transaction.atomic
def add_student_to_batch(batch_id: str, student, *, actor, request=None) -> BatchEnrollment:
    # Row lock so two concurrent adds cannot both pass the capacity check.
    batch = Batch.objects.select_for_update().select_related("course").filter(pk=batch_id).first()
    if batch is None:
        raise NotFoundError("Batch not found.")
    if batch.status in CLOSED_BATCH_STATUSES:
        raise ConflictError("Students cannot be added to a closed batch.", code="BATCH_CLOSED")
    enrollment = CourseEnrollment.objects.filter(student=student, course=batch.course, access_status="active").first()
    if enrollment is None:
        raise ConflictError("The student does not have active access to this course.", code="NOT_ELIGIBLE")
    existing = BatchEnrollment.objects.filter(batch=batch, student=student).first()
    if existing is not None and existing.status != "withdrawn":
        raise ConflictError("The student is already in this batch.", code="DUPLICATE")
    seats = BatchEnrollment.objects.filter(batch=batch).exclude(status="withdrawn").count()
    if seats >= batch.capacity:
        raise ConflictError("This batch is at capacity.", code="FULL")

    if existing is not None:
        existing.status = "enrolled"
        existing.withdrawn_at = None
        existing.completed_at = None
        existing.enrolled_at = timezone.now()
        existing.save()
        membership = existing
    else:
        membership = BatchEnrollment.objects.create(batch=batch, student=student, status="enrolled")

    enrollment.batch = batch
    enrollment.save(update_fields=["batch", "updated_at"])
    add_history(batch, "student_added", f"{student.name} added to the batch.", actor)
    log_audit(
        actor=actor,
        action="batch.add_student",
        entity_type="batch",
        entity_id=batch.id,
        details=f"Added {student.name} to {batch.code}.",
        request=request,
    )
    return membership


@transaction.atomic
def withdraw_student_from_batch(
    batch: Batch, student_id: str, *, reason: str = "", actor, request=None
) -> BatchEnrollment:
    membership = (
        BatchEnrollment.objects.select_related("student")
        .filter(batch=batch, student_id=student_id)
        .exclude(status="withdrawn")
        .first()
    )
    if membership is None:
        raise NotFoundError("Batch enrollment not found.")
    if membership.status == "completed":
        raise ConflictError("Completed students cannot be withdrawn.", code="ALREADY_COMPLETED")
    membership.status = "withdrawn"
    membership.withdrawn_at = timezone.now()
    membership.save(update_fields=["status", "withdrawn_at", "updated_at"])
    CourseEnrollment.objects.filter(student_id=student_id, course=batch.course, batch=batch).update(batch=None)
    suffix = f" Reason: {reason}" if reason else ""
    add_history(batch, "student_withdrawn", f"{membership.student.name} withdrawn from the batch.{suffix}", actor)
    log_audit(
        actor=actor,
        action="batch.withdraw_student",
        entity_type="batch",
        entity_id=batch.id,
        details=f"Withdrew {membership.student.name} from {batch.code}.{suffix}",
        request=request,
    )
    return membership


# ---------------------------------------------------------------------------
# Live sessions (admin)
# ---------------------------------------------------------------------------


@transaction.atomic
def create_session(data: dict, *, actor, request=None) -> LiveSession:
    course = data["course"]
    batch = data.get("batch")
    session = LiveSession.objects.create(
        course=course,
        batch=batch,
        title=data["title"],
        trainer=data["trainer"],
        starts_at=data["starts_at"],
        duration_minutes=data.get("duration_minutes", 60),
        status="scheduled",
        join_window_minutes_before=data.get("join_window_minutes_before", 15),
        description=data.get("description", ""),
        lesson=data.get("lesson"),
    )
    if batch is not None:
        add_history(batch, "session_scheduled", f'Session "{session.title}" scheduled.', actor)
    log_audit(
        actor=actor,
        action="session.create",
        entity_type="live_session",
        entity_id=session.id,
        details=f'Scheduled "{session.title}" for {course.title}.',
        request=request,
    )
    return session


@transaction.atomic
def cancel_session(session: LiveSession, *, actor, request=None) -> LiveSession:
    if session.status == "completed":
        raise ConflictError("Completed sessions cannot be cancelled.", code="ALREADY_COMPLETED")
    if session.status != "cancelled":
        session.status = "cancelled"
        session.save(update_fields=["status", "updated_at"])
        if session.batch_id:
            add_history(session.batch, "session_cancelled", f'Session "{session.title}" cancelled.', actor)
    log_audit(
        actor=actor,
        action="session.cancel",
        entity_type="live_session",
        entity_id=session.id,
        details=f'Cancelled "{session.title}".',
        request=request,
    )
    return session


def _live_lesson_for(session: LiveSession):
    if session.lesson_id:
        return session.lesson
    return (
        Lesson.objects.filter(module__course_id=session.course_id, type="live", title=session.title)
        .order_by("module__order", "order")
        .first()
    )


@transaction.atomic
def record_attendance(session: LiveSession, records: list[dict], *, actor, request=None) -> dict:
    """
    Upsert AttendanceRecord rows, mark the session completed, complete the linked
    live lesson for present/late students and re-evaluate course completion.
    """
    from apps.accounts.models import User

    if session.status == "cancelled":
        raise ConflictError("Cannot record attendance for a cancelled session.", code="CANCELLED")
    student_ids = [r["student_id"] for r in records]
    students = {u.id: u for u in User.objects.filter(pk__in=student_ids, role="student")}
    missing = [sid for sid in student_ids if sid not in students]
    if missing:
        raise ValidationFailed(
            "Unknown student id(s).", details={"records": [f"Unknown student: {sid}" for sid in missing]}
        )

    now = timezone.now()
    for rec in records:
        AttendanceRecord.objects.update_or_create(
            session=session,
            student=students[rec["student_id"]],
            defaults={"status": rec["status"], "recorded_by": actor if getattr(actor, "pk", None) else None},
        )

    session.status = "completed"
    session.save(update_fields=["status", "updated_at"])

    lesson = _live_lesson_for(session)
    if lesson is not None:
        for rec in records:
            if rec["status"] in ("present", "late"):
                LessonProgress.objects.update_or_create(
                    student=students[rec["student_id"]],
                    lesson=lesson,
                    defaults={"course": session.course, "completed": True, "completed_at": now},
                )

    for sid in student_ids:
        enrollment_services.evaluate_completion(students[sid], session.course)

    if session.batch_id:
        add_history(
            session.batch,
            "attendance_recorded",
            f'Attendance recorded for "{session.title}" ({len(records)} students).',
            actor,
        )
        add_history(session.batch, "session_completed", f'Session "{session.title}" completed.', actor)
    log_audit(
        actor=actor,
        action="session.attendance",
        entity_type="live_session",
        entity_id=session.id,
        details=f'Recorded attendance for "{session.title}" ({len(records)} records).',
        request=request,
    )
    return {"session_id": session.id, "recorded": len(records)}


@transaction.atomic
def upsert_recording(session: LiveSession, data: dict, *, actor, request=None) -> Recording:
    recording = Recording.objects.filter(session=session).first()
    if recording is None:
        recording = Recording(
            session=session,
            course=session.course,
            title=session.title,
            recorded_at=session.starts_at,
            duration_minutes=session.duration_minutes,
        )
    recording.status = data["status"]
    if "storage_key" in data:
        recording.storage_key = data["storage_key"]
    if data.get("duration_minutes"):
        recording.duration_minutes = data["duration_minutes"]
    if data.get("title"):
        recording.title = data["title"]
    if "lesson" in data:
        recording.lesson = data["lesson"]
    recording.save()
    log_audit(
        actor=actor,
        action="recording.update",
        entity_type="recording",
        entity_id=recording.id,
        details=f'Recording for "{session.title}" set to {recording.status}.',
        request=request,
    )
    return recording


# ---------------------------------------------------------------------------
# Student: join and playback
# ---------------------------------------------------------------------------


@transaction.atomic
def join_session(student, session_id: str) -> dict:
    session = sessions_visible_to(student).filter(pk=session_id).first()
    if session is None:
        raise NotFoundError("Session not found.")
    if session.status == "cancelled":
        raise ConflictError("This session has been cancelled.", code="SESSION_CANCELLED")
    if not can_join_now(session, visible=True):
        raise ForbiddenError(
            f"Joining opens {session.join_window_minutes_before} minutes before the class starts.",
            code="OUTSIDE_JOIN_WINDOW",
        )
    if session.status == "scheduled":
        session.status = "live"
        session.save(update_fields=["status", "updated_at"])
    provider = get_provider()
    join_url, expires_at = provider.create_join_url(session, student)
    return {"join_url": join_url, "provider": provider.name, "expires_at": expires_at}


def make_recording_token(recording: Recording, user) -> str:
    return signing.dumps({"rid": recording.id, "uid": user.id}, salt=RECORDING_TOKEN_SALT)


def read_recording_token(token: str) -> dict:
    """Raises signing.SignatureExpired / signing.BadSignature."""
    return signing.loads(token, salt=RECORDING_TOKEN_SALT, max_age=signed_url_ttl())


def recording_stream_path(token: str) -> str:
    return f"/api/v1/media/recordings/{token}"


def playback(student, recording_id: str) -> dict:
    recording = recordings_visible_to(student).filter(pk=recording_id).first()
    if recording is None:
        raise NotFoundError("Recording not found.")
    if recording.status != "available":
        raise ConflictError("This recording is not available.", code="RECORDING_UNAVAILABLE")
    resume = 0
    if recording.lesson_id:
        resume = (
            LessonProgress.objects.filter(student=student, lesson_id=recording.lesson_id)
            .values_list("last_position_seconds", flat=True)
            .first()
            or 0
        )
    expires_at = timezone.now() + signed_url_ttl()
    return {
        "stream_url": recording_stream_path(make_recording_token(recording, student)),
        "expires_at": expires_at,
        "resume_position_seconds": int(resume),
    }
