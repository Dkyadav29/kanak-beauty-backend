"""Fixtures for certificate tests. compute_progress is stubbed: the enrollments app owns it."""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.enrollments import services as enrollment_services
from apps.enrollments.services import ProgressSummary


def _summary(course, *, eligible: bool) -> ProgressSummary:
    return ProgressSummary(
        course_id=course.id,
        completed_lessons=4 if eligible else 2,
        total_lessons=5,
        required_lessons=4,
        completed_required_lessons=4 if eligible else 2,
        progress_percent=80 if eligible else 40,
        last_lesson_id=None,
        lessons=[],
        live_attendance_percent=100 if eligible else 50,
        assessment_passed=eligible,
        completion_eligible=eligible,
        is_completed=False,
        completed_at=None,
    )


@pytest.fixture
def eligible(monkeypatch):
    """Every student is reported eligible by compute_progress."""
    calls: list = []

    def fake(student, course):
        calls.append((student.id, course.id))
        return _summary(course, eligible=True)

    monkeypatch.setattr(enrollment_services, "compute_progress", fake)
    return calls


@pytest.fixture
def ineligible(monkeypatch):
    monkeypatch.setattr(
        enrollment_services, "compute_progress", lambda student, course: _summary(course, eligible=False)
    )


@pytest.fixture
def certificate(db, enrollment, student, course, batch):
    """An issued certificate for `student` in `course` (trusted issuance, no eligibility call)."""
    from apps.certificates import services

    return services.issue_certificate(student, course, batch, trusted=True)


@pytest.fixture
def revoked_certificate(db, certificate, admin_user):
    from apps.certificates import services

    return services.revoke_certificate(
        certificate, reason="Issued in error: assessment not completed", actor=admin_user
    )


@pytest.fixture
def other_enrollment(db, other_student, recorded_course):
    """Active enrollment for `other_student` in the recorded course (no batch)."""
    from decimal import Decimal

    from apps.enrollments.models import CourseEnrollment
    from apps.payments.models import PaymentTransaction

    payment = PaymentTransaction.objects.create(
        order_id="KBA-ORD-2001",
        student=other_student,
        course=recorded_course,
        amount=recorded_course.effective_price,
        list_price=recorded_course.effective_price,
        status="paid",
        method="Card",
        gateway_provider="sandbox",
        gateway_reference="gw_test_2001",
        checkout_token="chk_test_2001",
        invoice_available=True,
        paid_at=timezone.now(),
        referral_discount=Decimal("0"),
    )
    return CourseEnrollment.objects.create(
        student=other_student, course=recorded_course, source="purchase", access_status="active", payment=payment
    )
