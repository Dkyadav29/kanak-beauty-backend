"""Reusable certificate querysets and the eligible-student computation."""

from __future__ import annotations

from django.db.models import Q
from django.utils import timezone

from apps.enrollments.models import CourseEnrollment

from .models import Certificate


def certificates_for_student(student):
    return Certificate.objects.filter(student=student).order_by("-issued_at")


def admin_certificates(*, search: str | None = None, status: str | None = None, course_id: str | None = None):
    qs = Certificate.objects.all().order_by("-issued_at")
    if status in ("issued", "revoked"):
        qs = qs.filter(status=status)
    if course_id:
        qs = qs.filter(course_id=course_id)
    if search:
        q = search.strip()
        qs = qs.filter(Q(certificate_number__icontains=q) | Q(student_name__icontains=q) | Q(course_title__icontains=q))
    return qs


def eligible_students() -> list[dict]:
    """
    Active enrollments on certificate-enabled courses, without an issued certificate,
    whose server-side progress says completion criteria are met.
    """
    from apps.enrollments import services as enrollment_services

    issued = set(Certificate.objects.filter(status="issued").values_list("student_id", "course_id"))
    enrollments = (
        CourseEnrollment.objects.filter(access_status="active", course__certificate_enabled=True)
        .select_related("student", "course")
        .order_by("enrolled_at")
    )
    rows: list[dict] = []
    for enrollment in enrollments:
        if (enrollment.student_id, enrollment.course_id) in issued:
            continue
        progress = enrollment_services.compute_progress(enrollment.student, enrollment.course)
        if not progress.completion_eligible:
            continue
        rows.append(
            {
                "student_id": enrollment.student_id,
                "student_name": enrollment.student.name,
                "student_email": enrollment.student.email,
                "course_id": enrollment.course_id,
                "course_title": enrollment.course.title,
                "batch_id": enrollment.batch_id,
                "completed_at": enrollment.completed_at or timezone.now(),
                "evidence": {
                    "required_lessons_completed": f"{progress.completed_required_lessons}/{progress.required_lessons}",
                    "live_attendance_percent": progress.live_attendance_percent,
                    "assessment_passed": progress.assessment_passed,
                },
            }
        )
    return rows
