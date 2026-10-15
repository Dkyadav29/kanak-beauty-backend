"""Enrollments: course access records and per-lesson learning progress."""

from django.conf import settings
from django.db import models

from common.models import PrefixedIDModel


class CourseEnrollment(PrefixedIDModel):
    """
    Access to a course. Created only by a verified payment (source=purchase) or an
    audited admin grant. Purchase != completion: ``completed_at`` is set by the
    completion service when the configured criteria are met.
    """

    ID_PREFIX = "enr"

    SOURCE_CHOICES = [("purchase", "Purchase"), ("admin_grant", "Admin grant")]
    ACCESS_CHOICES = [
        ("pending_payment", "Pending payment"),
        ("active", "Active"),
        ("suspended", "Suspended"),
        ("revoked", "Revoked"),
    ]

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="enrollments")
    course = models.ForeignKey("courses.Course", on_delete=models.PROTECT, related_name="enrollments")
    batch = models.ForeignKey(
        "batches.Batch", null=True, blank=True, on_delete=models.SET_NULL, related_name="course_enrollments"
    )
    source = models.CharField(max_length=16, choices=SOURCE_CHOICES)
    access_status = models.CharField(max_length=16, choices=ACCESS_CHOICES, default="active", db_index=True)
    payment = models.ForeignKey(
        "payments.PaymentTransaction", null=True, blank=True, on_delete=models.SET_NULL, related_name="enrollments"
    )
    enrolled_at = models.DateTimeField(auto_now_add=True, db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "enrollments_course_enrollment"
        constraints = [models.UniqueConstraint(fields=["student", "course"], name="enrollment_student_course_unique")]
        indexes = [models.Index(fields=["course", "access_status"])]

    @property
    def is_active(self) -> bool:
        return self.access_status == "active"


class LessonProgress(PrefixedIDModel):
    ID_PREFIX = "lp"

    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lesson_progress")
    course = models.ForeignKey("courses.Course", on_delete=models.CASCADE, related_name="lesson_progress")
    lesson = models.ForeignKey("courses.Lesson", on_delete=models.CASCADE, related_name="progress")
    completed = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)
    last_position_seconds = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "enrollments_lesson_progress"
        constraints = [models.UniqueConstraint(fields=["student", "lesson"], name="progress_student_lesson_unique")]
        indexes = [models.Index(fields=["student", "course"])]
