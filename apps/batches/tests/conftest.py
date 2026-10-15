"""Fixtures local to the batches app tests."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone


@pytest.fixture
def completion_spy(monkeypatch):
    """Replace enrollment_services.evaluate_completion with a recording stub."""
    from apps.enrollments import services as enrollment_services

    calls: list[tuple[str, str]] = []

    def _stub(student, course):
        calls.append((student.id, course.id))
        return False

    monkeypatch.setattr(enrollment_services, "evaluate_completion", _stub)
    return calls


@pytest.fixture
def other_batch(db, course, trainer):
    from apps.batches.models import Batch

    today = timezone.localdate()
    return Batch.objects.create(
        code="KBA-MK-04",
        name="Makeup Artistry - Batch 4",
        course=course,
        trainer=trainer,
        start_date=today,
        end_date=today + timedelta(days=60),
        capacity=2,
        status="scheduled",
    )


@pytest.fixture
def other_enrollment(db, other_student, course):
    """Active enrollment for other_student in `course` without any batch membership."""
    from apps.enrollments.models import CourseEnrollment

    return CourseEnrollment.objects.create(
        student=other_student, course=course, source="admin_grant", access_status="active"
    )


@pytest.fixture
def course_wide_session(db, course, trainer):
    from apps.batches.models import LiveSession

    return LiveSession.objects.create(
        course=course,
        batch=None,
        title="Open Q&A",
        trainer=trainer,
        starts_at=timezone.now() + timedelta(days=2),
        duration_minutes=60,
        status="scheduled",
    )
