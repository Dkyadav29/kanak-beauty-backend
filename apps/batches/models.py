"""Batches: cohorts, batch membership, history timeline, live sessions, attendance, recordings."""

from django.conf import settings
from django.db import models

from common.models import PrefixedIDModel


class Batch(PrefixedIDModel):
    ID_PREFIX = "bt"

    STATUS_CHOICES = [
        ("scheduled", "Scheduled"),
        ("active", "Active"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
        ("archived", "Archived"),
    ]
    # Allowed transitions (enforced in services). Nothing is ever deleted.
    TRANSITIONS = {
        "scheduled": ("active", "cancelled"),
        "active": ("completed", "cancelled"),
        "completed": ("archived",),
        "cancelled": ("archived",),
        "archived": (),
    }

    code = models.CharField(max_length=24, unique=True)
    name = models.CharField(max_length=160)
    course = models.ForeignKey("courses.Course", on_delete=models.PROTECT, related_name="batches")
    trainer = models.ForeignKey("courses.Trainer", on_delete=models.PROTECT, related_name="batches")
    start_date = models.DateField(db_index=True)
    end_date = models.DateField()
    capacity = models.PositiveSmallIntegerField()
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="scheduled", db_index=True)
    schedule_note = models.CharField(max_length=240, blank=True, default="")

    class Meta:
        db_table = "batches_batch"
        ordering = ["-start_date"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(end_date__gte=models.F("start_date")), name="batch_end_after_start"
            ),
            models.CheckConstraint(condition=models.Q(capacity__gte=1), name="batch_capacity_positive"),
        ]

    def __str__(self) -> str:
        return f"{self.code} {self.name}"


class BatchEnrollment(PrefixedIDModel):
    ID_PREFIX = "ben"

    STATUS_CHOICES = [
        ("enrolled", "Enrolled"),
        ("in_progress", "In progress"),
        ("completed", "Completed"),
        ("withdrawn", "Withdrawn"),
    ]

    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="memberships")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="batch_memberships")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="enrolled", db_index=True)
    enrolled_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    withdrawn_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "batches_enrollment"
        constraints = [models.UniqueConstraint(fields=["batch", "student"], name="batch_student_unique")]


class BatchHistoryEvent(PrefixedIDModel):
    ID_PREFIX = "bh"

    TYPE_CHOICES = [
        ("batch_created", "Batch created"),
        ("batch_updated", "Batch updated"),
        ("status_changed", "Status changed"),
        ("student_added", "Student added"),
        ("student_withdrawn", "Student withdrawn"),
        ("student_completed", "Student completed"),
        ("session_scheduled", "Session scheduled"),
        ("session_completed", "Session completed"),
        ("session_cancelled", "Session cancelled"),
        ("attendance_recorded", "Attendance recorded"),
        ("certificate_issued", "Certificate issued"),
        ("certificate_revoked", "Certificate revoked"),
    ]

    batch = models.ForeignKey(Batch, on_delete=models.PROTECT, related_name="history")
    type = models.CharField(max_length=32, choices=TYPE_CHOICES, db_index=True)
    description = models.CharField(max_length=400)
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    actor_name = models.CharField(max_length=120, default="System")
    at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        db_table = "batches_history_event"
        ordering = ["-at"]


class LiveSession(PrefixedIDModel):
    ID_PREFIX = "ses"

    STATUS_CHOICES = [
        ("scheduled", "Scheduled"),
        ("live", "Live"),
        ("completed", "Completed"),
        ("cancelled", "Cancelled"),
    ]

    course = models.ForeignKey("courses.Course", on_delete=models.PROTECT, related_name="live_sessions")
    batch = models.ForeignKey(Batch, null=True, blank=True, on_delete=models.PROTECT, related_name="sessions")
    title = models.CharField(max_length=160)
    trainer = models.ForeignKey("courses.Trainer", on_delete=models.PROTECT, related_name="live_sessions")
    starts_at = models.DateTimeField(db_index=True)
    duration_minutes = models.PositiveSmallIntegerField(default=60)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="scheduled", db_index=True)
    join_window_minutes_before = models.PositiveSmallIntegerField(default=15)
    description = models.CharField(max_length=400, blank=True, default="")
    # Provider-side meeting reference used to mint signed join links. Never serialised to clients.
    provider_meeting_ref = models.CharField(max_length=255, blank=True, default="")
    # Optional curriculum link: attending completes this lesson.
    lesson = models.ForeignKey(
        "courses.Lesson", null=True, blank=True, on_delete=models.SET_NULL, related_name="live_sessions"
    )

    class Meta:
        db_table = "batches_live_session"
        ordering = ["starts_at"]


class AttendanceRecord(PrefixedIDModel):
    ID_PREFIX = "att"

    STATUS_CHOICES = [("present", "Present"), ("absent", "Absent"), ("late", "Late")]

    session = models.ForeignKey(LiveSession, on_delete=models.PROTECT, related_name="attendance")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="attendance")
    status = models.CharField(max_length=8, choices=STATUS_CHOICES)
    recorded_at = models.DateTimeField(auto_now=True)
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    class Meta:
        db_table = "batches_attendance"
        constraints = [models.UniqueConstraint(fields=["session", "student"], name="attendance_session_student_unique")]


class Recording(PrefixedIDModel):
    ID_PREFIX = "rec"

    STATUS_CHOICES = [("available", "Available"), ("processing", "Processing"), ("unavailable", "Unavailable")]

    session = models.OneToOneField(LiveSession, on_delete=models.PROTECT, related_name="recording")
    course = models.ForeignKey("courses.Course", on_delete=models.PROTECT, related_name="recordings")
    title = models.CharField(max_length=160)
    recorded_at = models.DateTimeField()
    duration_minutes = models.PositiveSmallIntegerField(default=60)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="processing", db_index=True)
    lesson = models.ForeignKey(
        "courses.Lesson", null=True, blank=True, on_delete=models.SET_NULL, related_name="recordings"
    )
    # Media storage reference; the playback endpoint returns a signed, expiring URL.
    storage_key = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        db_table = "batches_recording"
        ordering = ["-recorded_at"]
