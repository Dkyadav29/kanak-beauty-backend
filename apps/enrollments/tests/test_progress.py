"""Progress computation, completion eligibility matrix and completion side effects."""

from __future__ import annotations

import pytest

from apps.batches.models import BatchEnrollment, BatchHistoryEvent
from apps.enrollments import services
from apps.enrollments.models import CourseEnrollment, LessonProgress
from common.exceptions import ForbiddenError

pytestmark = pytest.mark.django_db

REQUIRED_TITLES = ("Welcome & Kit", "Colour Theory", "Live Practical: Base Work", "Final Assessment")


def _complete_required(complete_lesson, student, course, lessons):
    for title in REQUIRED_TITLES:
        complete_lesson(student, course, lessons[title])


def test_initial_progress_summary(enrollment, student, course):
    summary = services.compute_progress(student, course)
    assert summary.course_id == course.id
    assert summary.total_lessons == 5
    assert summary.required_lessons == 4
    assert summary.completed_lessons == 0
    assert summary.completed_required_lessons == 0
    assert summary.progress_percent == 0
    assert summary.last_lesson_id is None
    assert summary.lessons == []
    assert summary.live_attendance_percent is None  # no completed sessions yet
    assert summary.assessment_passed is False  # assessment required, lesson exists, not completed
    assert summary.completion_eligible is False
    assert summary.is_completed is False
    assert summary.completed_at is None


def test_progress_percent_rounds_half_up(enrollment, student, course, lessons, complete_lesson):
    complete_lesson(student, course, lessons["Welcome & Kit"])
    complete_lesson(student, course, lessons["Colour Theory"])
    summary = services.compute_progress(student, course)
    assert summary.completed_lessons == 2
    assert summary.progress_percent == 40
    assert summary.last_lesson_id == lessons["Colour Theory"].id
    assert [p.lesson_id for p in summary.lessons][0] == lessons["Colour Theory"].id


def test_not_eligible_without_completed_sessions(enrollment, student, course, lessons, complete_lesson):
    _complete_required(complete_lesson, student, course, lessons)
    summary = services.compute_progress(student, course)
    assert summary.completed_required_lessons == 4
    assert summary.assessment_passed is True
    assert summary.live_attendance_percent is None
    assert summary.completion_eligible is False


def test_eligible_when_attendance_meets_threshold(
    enrollment, batch, student, course, lessons, complete_lesson, completed_session_factory
):
    _complete_required(complete_lesson, student, course, lessons)
    completed_session_factory(batch=batch, student=student, status="late")
    summary = services.compute_progress(student, course)
    assert summary.live_attendance_percent == 100
    assert summary.completion_eligible is True


def test_not_eligible_below_attendance_threshold(
    enrollment, batch, student, course, lessons, complete_lesson, completed_session_factory
):
    _complete_required(complete_lesson, student, course, lessons)
    completed_session_factory(batch=batch, student=student, status="present", days_ago=10)
    completed_session_factory(batch=batch, student=student, status="absent", days_ago=5)
    completed_session_factory(batch=batch, student=None, days_ago=3)
    summary = services.compute_progress(student, course)
    assert summary.live_attendance_percent == 33
    assert summary.completion_eligible is False


def test_attendance_is_batch_scoped(
    enrollment, batch, student, course, trainer, lessons, complete_lesson, completed_session_factory
):
    from datetime import date, timedelta

    from apps.batches.models import Batch

    other_batch = Batch.objects.create(
        code="KBA-MK-04",
        name="Batch 4",
        course=course,
        trainer=trainer,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=10),
        capacity=10,
    )
    _complete_required(complete_lesson, student, course, lessons)
    # Only a session in another batch has been completed: unevaluable for this student's batch.
    completed_session_factory(batch=other_batch, student=None)
    assert services.compute_progress(student, course).live_attendance_percent is None
    completed_session_factory(batch=batch, student=student)
    assert services.compute_progress(student, course).live_attendance_percent == 100


def test_attendance_counts_all_sessions_when_enrollment_has_no_batch(
    student, course, batch, lessons, complete_lesson, completed_session_factory
):
    CourseEnrollment.objects.create(student=student, course=course, source="admin_grant", access_status="active")
    completed_session_factory(batch=batch, student=student)
    completed_session_factory(batch=None, student=None, days_ago=2)
    assert services.compute_progress(student, course).live_attendance_percent == 50


def test_missing_required_lesson_blocks_eligibility(
    enrollment, batch, student, course, lessons, complete_lesson, completed_session_factory
):
    for title in REQUIRED_TITLES[:-1]:
        complete_lesson(student, course, lessons[title])
    complete_lesson(student, course, lessons["Bonus: Social Media"])  # optional lesson does not count
    completed_session_factory(batch=batch, student=student)
    summary = services.compute_progress(student, course)
    assert summary.completed_required_lessons == 3
    assert summary.assessment_passed is False
    assert summary.completion_eligible is False


def test_assessment_not_required_reports_none(
    enrollment, batch, student, course, lessons, complete_lesson, completed_session_factory
):
    course.require_assessment = False
    course.save()
    for title in REQUIRED_TITLES[:-1]:
        complete_lesson(student, course, lessons[title])
    completed_session_factory(batch=batch, student=student)
    summary = services.compute_progress(student, course)
    assert summary.assessment_passed is None
    assert summary.completion_eligible is False  # the assessment lesson is still a required lesson
    complete_lesson(student, course, lessons["Final Assessment"])
    assert services.compute_progress(student, course).completion_eligible is True


def test_recorded_course_without_attendance_rule(recorded_enrollment, student, recorded_course, complete_lesson):
    from apps.courses.models import Lesson

    lessons = list(Lesson.objects.filter(module__course=recorded_course))
    complete_lesson(student, recorded_course, lessons[0])
    summary = services.compute_progress(student, recorded_course)
    assert summary.live_attendance_percent is None
    assert summary.assessment_passed is None
    assert summary.completion_eligible is False
    complete_lesson(student, recorded_course, lessons[1])
    summary = services.compute_progress(student, recorded_course)
    assert summary.progress_percent == 100
    assert summary.completion_eligible is True


def test_suspended_enrollment_is_never_eligible(
    enrollment, batch, student, course, lessons, complete_lesson, completed_session_factory
):
    _complete_required(complete_lesson, student, course, lessons)
    completed_session_factory(batch=batch, student=student)
    enrollment.access_status = "suspended"
    enrollment.save()
    assert services.compute_progress(student, course).completion_eligible is False
    assert services.evaluate_completion(student, course) is False


def test_live_lesson_cannot_be_completed_manually(enrollment, student, course, lessons):
    with pytest.raises(ForbiddenError) as exc:
        services.update_lesson_progress(
            student, course, lessons["Live Practical: Base Work"], completed=True, last_position_seconds=None
        )
    assert exc.value.code == "NOT_PERMITTED"
    assert exc.value.status_code == 403
    # Saving a playback position on a live lesson is allowed.
    summary = services.update_lesson_progress(
        student, course, lessons["Live Practical: Base Work"], completed=None, last_position_seconds=42
    )
    assert summary.lessons[0].last_position_seconds == 42
    assert summary.lessons[0].completed is False


def test_completion_marks_enrollment_batch_and_issues_certificate(
    enrollment, batch, student, course, lessons, complete_lesson, completed_session_factory, issue_certificate_mock
):
    for title in REQUIRED_TITLES[:-1]:
        complete_lesson(student, course, lessons[title])
    completed_session_factory(batch=batch, student=student)
    assert services.evaluate_completion(student, course) is False
    issue_certificate_mock.assert_not_called()

    summary = services.update_lesson_progress(
        student, course, lessons["Final Assessment"], completed=True, last_position_seconds=None
    )
    assert summary.is_completed is True
    assert summary.completed_at is not None
    enrollment.refresh_from_db()
    assert enrollment.completed_at is not None
    membership = BatchEnrollment.objects.get(batch=batch, student=student)
    assert membership.status == "completed"
    assert membership.completed_at is not None
    event = BatchHistoryEvent.objects.get(batch=batch, type="student_completed")
    assert event.actor is None
    assert event.actor_name == "System"
    assert student.name in event.description
    issue_certificate_mock.assert_called_once()
    args, kwargs = issue_certificate_mock.call_args
    assert args[0] == student
    assert args[1] == course
    assert args[2] == batch
    assert kwargs == {"trusted": True}
    # Re-evaluating an already completed enrollment is a no-op.
    assert services.evaluate_completion(student, course) is False
    issue_certificate_mock.assert_called_once()


def test_manual_approval_does_not_issue_certificate(
    recorded_enrollment, student, recorded_course, complete_lesson, issue_certificate_mock
):
    from apps.courses.models import Lesson

    recorded_course.certificate_issuance = "manual_approval"
    recorded_course.save()
    for lesson in Lesson.objects.filter(module__course=recorded_course):
        complete_lesson(student, recorded_course, lesson)
    assert services.evaluate_completion(student, recorded_course) is True
    recorded_enrollment.refresh_from_db()
    assert recorded_enrollment.completed_at is not None
    issue_certificate_mock.assert_not_called()


def test_certificate_disabled_does_not_issue(
    recorded_enrollment, student, recorded_course, complete_lesson, issue_certificate_mock
):
    from apps.courses.models import Lesson

    recorded_course.certificate_enabled = False
    recorded_course.save()
    for lesson in Lesson.objects.filter(module__course=recorded_course):
        complete_lesson(student, recorded_course, lesson)
    assert services.evaluate_completion(student, recorded_course) is True
    issue_certificate_mock.assert_not_called()


def test_grant_access_for_payment_is_idempotent(student, recorded_course):
    from apps.payments.models import PaymentTransaction

    payment = PaymentTransaction.objects.create(
        order_id="KBA-ORD-9001",
        student=student,
        course=recorded_course,
        amount=1,
        list_price=1,
        status="paid",
        checkout_token="tok-9001",
    )
    first = services.grant_access_for_payment(payment)
    second = services.grant_access_for_payment(payment)
    assert first.id == second.id
    assert CourseEnrollment.objects.filter(student=student, course=recorded_course).count() == 1
    assert first.access_status == "active"
    assert first.source == "purchase"
    assert first.payment_id == payment.id
    # Re-purchase after a revoked enrollment re-activates the same row.
    first.access_status = "revoked"
    first.save()
    again = services.grant_access_for_payment(payment)
    assert again.id == first.id
    assert again.access_status == "active"


def test_revoke_access_for_refund_withdraws_from_batch(enrollment, batch, student, admin_user):
    services.revoke_access_for_refund(enrollment.payment, actor=admin_user)
    enrollment.refresh_from_db()
    assert enrollment.access_status == "revoked"
    membership = BatchEnrollment.objects.get(batch=batch, student=student)
    assert membership.status == "withdrawn"
    assert membership.withdrawn_at is not None
    event = BatchHistoryEvent.objects.get(batch=batch, type="student_withdrawn")
    assert event.actor == admin_user
    assert event.actor_name == admin_user.name
    assert services.is_entitled(student, enrollment.course_id) is False
    assert services.get_active_enrollment(student, enrollment.course_id) is None
    assert LessonProgress.objects.count() == 0
