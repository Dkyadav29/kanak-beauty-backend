"""
Reporting fixtures: a small academy with known numbers.

- student (Priya): paid, active, not completed enrollment in `course` (hybrid, batch), payment 29999 paid.
- other_student (Ananya): paid, active, COMPLETED enrollment in `recorded_course` with an issued
  certificate, plus a pending payment of 9999 for `course`.
- unverified_student (Zoya): no enrollments or payments.
- draft_course exists; `batch` is active; one joinable (upcoming) and one completed session.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from apps.enrollments import services as enrollment_services
from apps.enrollments.services import ProgressSummary


@pytest.fixture
def progress_stub(monkeypatch):
    def fake(student, course):
        return ProgressSummary(
            course_id=course.id,
            completed_lessons=3,
            total_lessons=5,
            required_lessons=4,
            completed_required_lessons=3,
            progress_percent=55,
            last_lesson_id=None,
            lessons=[],
            live_attendance_percent=None,
            assessment_passed=None,
            completion_eligible=True,
            is_completed=False,
            completed_at=None,
        )

    monkeypatch.setattr(enrollment_services, "compute_progress", fake)


@pytest.fixture
def completed_enrollment(db, other_student, recorded_course):
    from apps.certificates import services as certificate_services
    from apps.enrollments.models import CourseEnrollment
    from apps.payments.models import PaymentTransaction

    payment = PaymentTransaction.objects.create(
        order_id="KBA-ORD-3001",
        student=other_student,
        course=recorded_course,
        amount=recorded_course.effective_price,
        list_price=recorded_course.effective_price,
        status="paid",
        method="Card",
        gateway_provider="sandbox",
        gateway_reference="gw_test_3001",
        checkout_token="chk_test_3001",
        invoice_available=True,
        paid_at=timezone.now() - timedelta(days=3),
    )
    enr = CourseEnrollment.objects.create(
        student=other_student,
        course=recorded_course,
        source="purchase",
        access_status="active",
        payment=payment,
        completed_at=timezone.now() - timedelta(minutes=1),
    )
    certificate_services.issue_certificate(other_student, recorded_course, trusted=True)
    return enr


@pytest.fixture
def pending_payment(db, other_student, course):
    from apps.payments.models import PaymentTransaction

    return PaymentTransaction.objects.create(
        order_id="KBA-ORD-3002",
        student=other_student,
        course=course,
        amount=Decimal("9999.00"),
        list_price=course.effective_price,
        status="pending",
        gateway_provider="sandbox",
        checkout_token="chk_test_3002",
    )


@pytest.fixture
def attendance(db, completed_session_with_recording, student):
    from apps.batches.models import AttendanceRecord

    return AttendanceRecord.objects.create(session=completed_session_with_recording, student=student, status="present")


@pytest.fixture
def academy(
    db,
    admin_user,
    student,
    other_student,
    unverified_student,
    enrollment,
    completed_enrollment,
    pending_payment,
    draft_course,
    joinable_session,
    attendance,
):
    """The whole scenario in one fixture."""
    return {
        "student": student,
        "other_student": other_student,
        "unverified_student": unverified_student,
        "enrollment": enrollment,
        "completed_enrollment": completed_enrollment,
        "pending_payment": pending_payment,
    }
