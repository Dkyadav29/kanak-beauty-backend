"""
Certificate services (CONTRACT).

- issue_certificate() is the ONLY way a Certificate row is created. It re-verifies
  eligibility via apps.enrollments.services.compute_progress unless
  ``trusted=True`` (used by evaluate_completion, which has just verified it).
  It runs under transaction.atomic() + select_for_update on the enrollment so two
  concurrent requests cannot issue two active certificates for the same
  student+course (MariaDB has no partial unique index).
- Certificate numbers: f"{settings.CERTIFICATE_NUMBER_PREFIX}-{year}-{sequence:05d}",
  sequence unique per year, allocated under a row lock.
- revoke_certificate()/reissue_certificate() write AuditLog + BatchHistoryEvent.
"""

from __future__ import annotations

from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.batches.models import BatchHistoryEvent
from apps.core.services import client_ip, log_audit
from apps.enrollments.models import CourseEnrollment
from common.exceptions import ConflictError

from .models import Certificate, CertificateVerificationLog

DEFAULT_ISSUER = "Kanak Beauty Service"
# The demo/mock numbering starts after a reserved block so seeded numbers never collide.
SEQUENCE_OFFSET = 200


def _year_prefix(year: int | None = None) -> str:
    year = year or timezone.localtime().year
    return f"{settings.CERTIFICATE_NUMBER_PREFIX}-{year}-"


def next_certificate_number() -> str:
    """
    Next number for the current year: ``<PREFIX>-<year>-<seq:05d>`` where seq is
    (certificates already numbered for that year) + 1 + SEQUENCE_OFFSET.

    Must be called inside ``transaction.atomic()``: the year's rows are locked with
    ``select_for_update`` so concurrent issuers serialise on the sequence. A
    duplicate slipping through (gap insert) raises IntegrityError, which
    ``_create_certificate`` retries once.
    """
    prefix = _year_prefix()
    locked = (
        Certificate.objects.select_for_update()
        .filter(certificate_number__startswith=prefix)
        .values_list("id", flat=True)
    )
    count = len(list(locked))
    return f"{prefix}{count + 1 + SEQUENCE_OFFSET:05d}"


def _create_certificate(**fields) -> Certificate:
    """Create the row with a freshly allocated number; retry once on a number collision."""
    for attempt in (1, 2):
        try:
            with transaction.atomic():
                return Certificate.objects.create(certificate_number=next_certificate_number(), **fields)
        except IntegrityError:
            if attempt == 2:
                raise
    raise AssertionError("unreachable")  # pragma: no cover


def _actor_name(user) -> str:
    return getattr(user, "name", None) or "System"


def _issue(
    student,
    course,
    batch,
    *,
    issued_by_user,
    trusted: bool,
    fail_if_issued: bool,
    reissued_from: Certificate | None,
    audit_action: str,
    audit_details: str | None,
    request=None,
) -> Certificate:
    from apps.enrollments import services as enrollment_services

    with transaction.atomic():
        enrollment = CourseEnrollment.objects.select_for_update().filter(student=student, course=course).first()
        if enrollment is None or not enrollment.is_active:
            raise ConflictError("The student does not have active access to this course.", code="NOT_ENROLLED")
        if not course.certificate_enabled:
            raise ConflictError("Certificates are not enabled for this course.", code="NOT_ENABLED")

        existing = Certificate.objects.filter(student=student, course=course, status="issued").first()
        if existing is not None:
            if fail_if_issued:
                raise ConflictError(
                    "An active certificate already exists for this student and course.", code="ALREADY_ISSUED"
                )
            return existing

        if not trusted:
            progress = enrollment_services.compute_progress(student, course)
            if not progress.completion_eligible:
                raise ConflictError(
                    "The student has not met the completion criteria for this course.", code="NOT_ELIGIBLE"
                )

        now = timezone.now()
        if enrollment.completed_at is None:
            enrollment.completed_at = now
            enrollment.save(update_fields=["completed_at", "updated_at"])

        batch = batch if batch is not None else enrollment.batch
        issued_by = _actor_name(issued_by_user) if getattr(issued_by_user, "pk", None) else DEFAULT_ISSUER

        certificate = _create_certificate(
            student=student,
            student_name=student.name,
            course=course,
            course_title=course.title,
            batch=batch,
            completion_date=enrollment.completed_at,
            issued_by=issued_by,
            issued_by_user=issued_by_user if getattr(issued_by_user, "pk", None) else None,
            status="issued",
            reissued_from=reissued_from,
        )

        if batch is not None:
            BatchHistoryEvent.objects.create(
                batch=batch,
                type="certificate_issued",
                description=f"Certificate {certificate.certificate_number} issued to {student.name}.",
                actor=issued_by_user if getattr(issued_by_user, "pk", None) else None,
                actor_name=issued_by,
            )

        log_audit(
            actor=issued_by_user,
            action=audit_action,
            entity_type="certificate",
            entity_id=certificate.id,
            details=audit_details or f'Issued {certificate.certificate_number} to {student.name} for "{course.title}".',
            request=request,
        )
        return certificate


def issue_certificate(
    student,
    course,
    batch=None,
    *,
    issued_by_user=None,
    trusted: bool = False,
    fail_if_issued: bool = False,
    request=None,
):
    """
    Issue (or return the existing active) certificate. Raises ConflictError when not eligible.

    ``fail_if_issued=True`` (admin endpoint) raises 409 ALREADY_ISSUED instead of
    returning the existing certificate.
    """
    return _issue(
        student,
        course,
        batch,
        issued_by_user=issued_by_user,
        trusted=trusted,
        fail_if_issued=fail_if_issued,
        reissued_from=None,
        audit_action="certificate.issue",
        audit_details=None,
        request=request,
    )


def revoke_certificate(certificate, *, reason: str, actor, request=None):
    with transaction.atomic():
        certificate = Certificate.objects.select_for_update().select_related("batch").get(pk=certificate.pk)
        if certificate.status == "revoked":
            raise ConflictError("This certificate is already revoked.", code="ALREADY_REVOKED")
        certificate.status = "revoked"
        certificate.revoked_at = timezone.now()
        certificate.revoked_reason = reason
        certificate.revoked_by = actor if getattr(actor, "pk", None) else None
        certificate.save(update_fields=["status", "revoked_at", "revoked_reason", "revoked_by", "updated_at"])

        if certificate.batch_id:
            BatchHistoryEvent.objects.create(
                batch=certificate.batch,
                type="certificate_revoked",
                description=f"Certificate {certificate.certificate_number} revoked: {reason}"[:400],
                actor=certificate.revoked_by,
                actor_name=_actor_name(actor),
            )
        log_audit(
            actor=actor,
            action="certificate.revoke",
            entity_type="certificate",
            entity_id=certificate.id,
            details=f"Revoked {certificate.certificate_number} ({certificate.student_name}). Reason: {reason}",
            request=request,
        )
        return certificate


def reissue_certificate(certificate, *, reason: str, actor, request=None):
    """Only for revoked certificates whose holder still meets the criteria. Returns the new certificate."""
    from apps.enrollments import services as enrollment_services

    with transaction.atomic():
        old = (
            Certificate.objects.select_for_update().select_related("student", "course", "batch").get(pk=certificate.pk)
        )
        if old.status != "revoked":
            raise ConflictError("Only revoked certificates can be reissued.", code="NOT_REVOKED")
        enrollment = CourseEnrollment.objects.filter(
            student=old.student, course=old.course, access_status="active"
        ).first()
        if enrollment is None:
            raise ConflictError("The student does not have active access to this course.", code="NOT_ENROLLED")
        progress = enrollment_services.compute_progress(old.student, old.course)
        if not progress.completion_eligible:
            raise ConflictError("The student no longer meets the completion criteria.", code="NOT_ELIGIBLE")
        return _issue(
            old.student,
            old.course,
            old.batch,
            issued_by_user=actor,
            trusted=True,
            fail_if_issued=True,
            reissued_from=old,
            audit_action="certificate.reissue",
            audit_details=f"Reissued {old.certificate_number}. Reason: {reason}",
            request=request,
        )


def verify_certificate(certificate_number: str, *, request=None) -> dict:
    """Public verification result (valid is False for revoked/unknown). Logs the lookup."""
    number = (certificate_number or "").strip().upper()
    certificate = Certificate.objects.filter(certificate_number__iexact=number).first() if number else None
    checked_at = timezone.now()
    result = certificate.status if certificate is not None else "not_found"

    CertificateVerificationLog.objects.create(
        certificate_number=number[:32], result=result, ip_address=client_ip(request)
    )

    if certificate is None:
        return {"valid": False, "status": "not_found", "certificate": None, "checked_at": checked_at}
    return {
        "valid": certificate.status == "issued",
        "status": certificate.status,
        "certificate": {
            "certificate_number": certificate.certificate_number,
            "student_name": certificate.student_name,
            "course_title": certificate.course_title,
            "completion_date": certificate.completion_date,
            "issued_at": certificate.issued_at,
            "issued_by": certificate.issued_by,
            "status": certificate.status,
        },
        "checked_at": checked_at,
    }
