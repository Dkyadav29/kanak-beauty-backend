"""Fixtures shared by the enrollments tests."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock

import pytest
from django.utils import timezone


@pytest.fixture
def issue_certificate_mock(monkeypatch):
    """Stand-in for apps.certificates.services.issue_certificate (owned by another app)."""
    from apps.certificates import services as certificate_services

    mock = MagicMock(name="issue_certificate")
    monkeypatch.setattr(certificate_services, "issue_certificate", mock)
    return mock


@pytest.fixture
def lessons(course):
    """Lessons of `course` keyed by title."""
    from apps.courses.models import Lesson

    return {lesson.title: lesson for lesson in Lesson.objects.filter(module__course=course)}


@pytest.fixture
def complete_lesson(db):
    from apps.enrollments.models import LessonProgress

    def _complete(student, course, lesson, completed=True):
        row, _ = LessonProgress.objects.update_or_create(
            student=student,
            lesson=lesson,
            defaults={"course": course, "completed": completed, "completed_at": timezone.now() if completed else None},
        )
        return row

    return _complete


@pytest.fixture
def completed_session_factory(db, course, trainer):
    """Create a completed live session (optionally in a batch) with an attendance record for a student."""
    from apps.batches.models import AttendanceRecord, LiveSession

    def _make(batch=None, student=None, status="present", days_ago=7):
        session = LiveSession.objects.create(
            course=course,
            batch=batch,
            title=f"Completed session {days_ago}",
            trainer=trainer,
            starts_at=timezone.now() - timedelta(days=days_ago),
            duration_minutes=90,
            status="completed",
        )
        if student is not None and status is not None:
            AttendanceRecord.objects.create(session=session, student=student, status=status)
        return session

    return _make


@pytest.fixture
def recorded_enrollment(db, student, recorded_course):
    from apps.enrollments.models import CourseEnrollment

    return CourseEnrollment.objects.create(
        student=student, course=recorded_course, source="admin_grant", access_status="active"
    )
