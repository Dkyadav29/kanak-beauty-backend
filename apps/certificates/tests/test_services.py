"""Service-level rules used by other apps (automatic issuance from evaluate_completion)."""

import pytest

from apps.certificates import services
from apps.certificates.models import Certificate
from apps.core.models import AuditLog
from common.exceptions import ConflictError

pytestmark = pytest.mark.django_db


def test_trusted_issue_skips_eligibility_and_is_idempotent(enrollment, student, course, batch, monkeypatch):
    from apps.enrollments import services as enrollment_services

    def boom(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("compute_progress must not be called when trusted=True")

    monkeypatch.setattr(enrollment_services, "compute_progress", boom)
    first = services.issue_certificate(student, course, batch, trusted=True)
    second = services.issue_certificate(student, course, trusted=True)
    assert first.pk == second.pk
    assert Certificate.objects.count() == 1
    assert first.issued_by == "Kanak Beauty Service"
    assert first.issued_by_user is None
    assert first.batch == batch
    audit = AuditLog.objects.get(action="certificate.issue")
    assert audit.actor is None and audit.actor_name == "System"


def test_untrusted_issue_checks_eligibility(enrollment, student, course, ineligible):
    with pytest.raises(ConflictError) as exc:
        services.issue_certificate(student, course)
    assert exc.value.code == "NOT_ELIGIBLE"


def test_issue_without_enrollment(other_student, course, eligible):
    with pytest.raises(ConflictError) as exc:
        services.issue_certificate(other_student, course)
    assert exc.value.code == "NOT_ENROLLED"


def test_next_certificate_number_counts_only_current_year(enrollment, student, course, settings):
    settings.CERTIFICATE_NUMBER_PREFIX = "KBA"
    Certificate.objects.create(
        certificate_number="KBA-1999-00001",
        student=student,
        student_name=student.name,
        course=course,
        course_title=course.title,
        completion_date=enrollment.enrolled_at,
        status="revoked",  # an active certificate would simply be returned; a revoked one must not block issuance
    )
    cert = services.issue_certificate(student, course, trusted=True)
    assert cert.certificate_number.endswith("-00201")
    assert "-1999-" not in cert.certificate_number


def test_verify_service_logs_lookup(certificate):
    result = services.verify_certificate(f"  {certificate.certificate_number.lower()} ")
    assert result["valid"] is True and result["status"] == "issued"
    assert result["certificate"]["certificate_number"] == certificate.certificate_number
    missing = services.verify_certificate("nope")
    assert missing == {"valid": False, "status": "not_found", "certificate": None, "checked_at": missing["checked_at"]}
