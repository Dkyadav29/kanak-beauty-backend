"""Admin-side enrollment mutations (grant, access changes) with audit rows."""

from __future__ import annotations

from django.db import transaction
from django.db.models import Q

from apps.accounts.models import User
from apps.core.services import log_audit
from apps.courses.models import Course
from apps.enrollments.models import CourseEnrollment
from common.exceptions import ConflictError, NotFoundError
from common.permissions import ROLE_STUDENT


def admin_enrollments_queryset():
    return CourseEnrollment.objects.select_related("student", "course", "batch", "payment").order_by("-enrolled_at")


def filter_admin_enrollments(queryset, *, search=None, course_id=None, payment_status=None, access_status=None):
    if course_id:
        queryset = queryset.filter(course_id=course_id)
    if payment_status:
        queryset = (
            queryset.filter(payment__isnull=True)
            if payment_status == "none"
            else queryset.filter(payment__status=payment_status)
        )
    if access_status:
        queryset = queryset.filter(access_status=access_status)
    if search:
        queryset = queryset.filter(
            Q(student__name__icontains=search)
            | Q(student__email__icontains=search)
            | Q(payment__order_id__icontains=search)
            | Q(id__icontains=search)
        )
    return queryset


def grant_enrollment(*, actor, student_id: str, course_id: str, reason: str, request=None) -> CourseEnrollment:
    student = User.objects.filter(pk=student_id, role=ROLE_STUDENT).first()
    if student is None:
        raise NotFoundError("Student not found.")
    course = Course.objects.filter(Q(pk=course_id) | Q(slug=course_id)).first()
    if course is None:
        raise NotFoundError("Course not found.")
    with transaction.atomic():
        existing = CourseEnrollment.objects.select_for_update().filter(student=student, course=course).first()
        if existing is not None and existing.access_status == "active":
            raise ConflictError("The student already has active access.", code="ALREADY_ENROLLED")
        if existing is None:
            enrollment = CourseEnrollment.objects.create(
                student=student, course=course, source="admin_grant", access_status="active"
            )
        else:
            # One row per (student, course): an admin grant re-activates the existing record.
            existing.access_status = "active"
            existing.source = "admin_grant"
            existing.save(update_fields=["access_status", "source", "updated_at"])
            enrollment = existing
        log_audit(
            actor=actor,
            action="enrollment.grant",
            entity_type="enrollment",
            entity_id=enrollment.id,
            details=f'Granted {student.name} access to "{course.title}". Reason: {reason}',
            request=request,
        )
    return admin_enrollments_queryset().get(pk=enrollment.pk)


def set_enrollment_access(
    *, actor, enrollment_id: str, access_status: str, reason: str, request=None
) -> CourseEnrollment:
    with transaction.atomic():
        enrollment = CourseEnrollment.objects.select_for_update().filter(pk=enrollment_id).first()
        if enrollment is None:
            raise NotFoundError("Enrollment not found.")
        if enrollment.access_status in ("revoked", "pending_payment"):
            label = enrollment.access_status.replace("_", " ")
            raise ConflictError(f"Access cannot be changed for a {label} enrollment.", code="INVALID_TRANSITION")
        previous = enrollment.access_status
        enrollment.access_status = access_status
        enrollment.save(update_fields=["access_status", "updated_at"])
        log_audit(
            actor=actor,
            action=f"enrollment.{access_status}",
            entity_type="enrollment",
            entity_id=enrollment.id,
            details=f"Access {previous} -> {access_status}. Reason: {reason}",
            request=request,
        )
    return admin_enrollments_queryset().get(pk=enrollment.pk)
