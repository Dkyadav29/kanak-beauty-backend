"""Student endpoints: dashboard, enrollments, course, progress."""

from __future__ import annotations

import pytest
from django.utils import timezone

from apps.courses.models import LearningMaterial
from apps.enrollments.models import CourseEnrollment, LessonProgress

pytestmark = pytest.mark.django_db

BASE = "/api/v1/student"


def _issue_certificate(student, course, batch=None):
    from apps.certificates.models import Certificate

    return Certificate.objects.create(
        certificate_number=f"KBA-2026-{Certificate.objects.count() + 1:05d}",
        student=student,
        student_name=student.name,
        course=course,
        course_title=course.title,
        batch=batch,
        completion_date=timezone.now(),
    )


# --- Dashboard ---------------------------------------------------------------------------------


def test_dashboard_shape_and_stats(
    student_client,
    student,
    enrollment,
    batch,
    course,
    recorded_course,
    joinable_session,
    future_session,
    lessons,
    complete_lesson,
):
    complete_lesson(student, course, lessons["Welcome & Kit"])
    completed = CourseEnrollment.objects.create(
        student=student,
        course=recorded_course,
        source="admin_grant",
        access_status="active",
        completed_at=timezone.now(),
    )
    cert = _issue_certificate(student, recorded_course)
    res = student_client.get(f"{BASE}/dashboard")
    assert res.status_code == 200, res.content
    body = res.json()
    assert body["stats"] == {
        "totalEnrolled": 2,
        "active": 1,
        "upcomingLiveClasses": 2,
        "completed": 1,
        "certificates": 1,
    }
    assert body["continueLearning"]["courseId"] == course.id
    assert body["continueLearning"]["progressPercent"] == 20
    assert body["continueLearning"]["enrollment"]["id"] == enrollment.id
    assert [s["id"] for s in body["upcomingSessions"]] == [joinable_session.id, future_session.id]
    first = body["upcomingSessions"][0]
    assert first["isEnrolled"] is True
    assert first["canJoinNow"] is True
    assert first["courseTitle"] == course.title
    assert first["trainerName"] == course.trainer.name
    assert first["recordingId"] is None
    assert "joinOpensAt" in first and "joinClosesAt" in first
    assert body["upcomingSessions"][1]["canJoinNow"] is False
    assert {c["courseId"] for c in body["recentCourses"]} == {course.id, recorded_course.id}
    recent = body["recentCertificates"]
    assert len(recent) == 1
    assert recent[0]["id"] == cert.id
    assert recent[0]["verificationUrl"] == f"/verify/{cert.certificate_number}"
    assert recent[0]["revokedReason"] is None
    completed_view = next(v for v in body["recentCourses"] if v["courseId"] == recorded_course.id)
    assert completed_view["isCompleted"] is True
    assert completed_view["certificateId"] == cert.id
    assert completed.id == completed_view["enrollment"]["id"]


def test_dashboard_without_enrollments(student_client):
    res = student_client.get(f"{BASE}/dashboard")
    assert res.status_code == 200
    body = res.json()
    assert body["continueLearning"] is None
    assert body["stats"]["totalEnrolled"] == 0
    assert body["upcomingSessions"] == []


def test_dashboard_requires_student(admin_client, api):
    assert admin_client.get(f"{BASE}/dashboard").status_code == 403
    assert api.get(f"{BASE}/dashboard").status_code == 401


def test_dashboard_hides_sessions_of_other_batches(
    student_client, student, enrollment, course, trainer, future_session
):
    from datetime import date, timedelta

    from apps.batches.models import Batch, LiveSession

    other = Batch.objects.create(
        code="KBA-MK-09",
        name="Other",
        course=course,
        trainer=trainer,
        start_date=date.today(),
        end_date=date.today() + timedelta(days=5),
        capacity=5,
    )
    LiveSession.objects.create(
        course=course, batch=other, title="Other batch", trainer=trainer, starts_at=timezone.now() + timedelta(days=1)
    )
    LiveSession.objects.create(
        course=course, batch=None, title="Course-wide", trainer=trainer, starts_at=timezone.now() + timedelta(days=2)
    )
    body = student_client.get(f"{BASE}/dashboard").json()
    assert {s["title"] for s in body["upcomingSessions"]} == {"Course-wide", future_session.title}


# --- Enrollments -------------------------------------------------------------------------------


def test_enrollments_list_shape_and_exclusions(
    student_client, student, enrollment, course, recorded_course, future_session
):
    CourseEnrollment.objects.create(
        student=student, course=recorded_course, source="purchase", access_status="pending_payment"
    )
    res = student_client.get(f"{BASE}/enrollments")
    assert res.status_code == 200
    rows = res.json()
    assert len(rows) == 1
    row = rows[0]
    assert row["courseId"] == course.id
    assert row["title"] == course.title
    assert row["trainerName"] == course.trainer.name
    assert row["format"] == "hybrid"
    assert row["totalLessons"] == 5
    assert row["completedLessons"] == 0
    assert row["progressPercent"] == 0
    assert row["isCompleted"] is False
    assert row["certificateId"] is None
    assert row["nextSessionAt"] is not None
    assert row["enrollment"]["accessStatus"] == "active"
    assert row["enrollment"]["paymentId"] == enrollment.payment_id
    assert row["enrollment"]["batchId"] == enrollment.batch_id


def test_enrollments_list_query_count_is_bounded(
    student_client, student, enrollment, recorded_course, future_session, django_assert_max_num_queries
):
    CourseEnrollment.objects.create(
        student=student, course=recorded_course, source="admin_grant", access_status="active"
    )
    with django_assert_max_num_queries(14):
        res = student_client.get(f"{BASE}/enrollments")
    assert res.status_code == 200
    assert len(res.json()) == 2


def test_enrollments_requires_student_role(admin_client, api):
    assert admin_client.get(f"{BASE}/enrollments").status_code == 403
    assert api.get(f"{BASE}/enrollments").status_code == 401


# --- Course ------------------------------------------------------------------------------------


def test_course_detail_for_enrolled_student(student_client, enrollment, course, lessons):
    LearningMaterial.objects.create(
        lesson=lessons["Welcome & Kit"],
        title="Kit checklist",
        type="pdf",
        size_label="1.2 MB",
        storage_key="secret/key.pdf",
    )
    res = student_client.get(f"{BASE}/courses/{course.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["id"] == course.id
    assert body["trainer"]["name"] == course.trainer.name
    assert body["completionCriteria"] == {
        "requireAllRequiredLessons": True,
        "minLiveAttendancePercent": 75,
        "requireAssessment": True,
    }
    assert [m["title"] for m in body["modules"]] == ["Foundations", "Client-ready looks"]
    lesson = body["modules"][0]["lessons"][0]
    assert lesson["title"] == "Welcome & Kit"
    assert lesson["moduleId"] == body["modules"][0]["id"]
    assert lesson["materials"] == [
        {"id": lesson["materials"][0]["id"], "title": "Kit checklist", "type": "pdf", "sizeLabel": "1.2 MB"}
    ]
    assert "storageKey" not in lesson["materials"][0]
    assert "videoStorageKey" not in lesson
    # Slug lookup works too.
    assert student_client.get(f"{BASE}/courses/{course.slug}").status_code == 200


def test_course_detail_requires_active_enrollment(
    other_student_client, student_client, enrollment, course, recorded_course
):
    res = other_student_client.get(f"{BASE}/courses/{course.id}")
    assert res.status_code == 403
    assert res.json()["code"] == "NOT_ENROLLED"
    assert student_client.get(f"{BASE}/courses/{recorded_course.id}").status_code == 403
    enrollment.access_status = "suspended"
    enrollment.save()
    assert student_client.get(f"{BASE}/courses/{course.id}").status_code == 403


def test_course_detail_unknown_and_auth(student_client, admin_client, api, course):
    res = student_client.get(f"{BASE}/courses/crs_unknown")
    assert res.status_code == 404
    assert res.json()["code"] == "NOT_FOUND"
    assert admin_client.get(f"{BASE}/courses/{course.id}").status_code == 403
    assert api.get(f"{BASE}/courses/{course.id}").status_code == 401


# --- Progress ----------------------------------------------------------------------------------


def test_progress_endpoint(student_client, enrollment, student, course, lessons, complete_lesson):
    complete_lesson(student, course, lessons["Welcome & Kit"])
    res = student_client.get(f"{BASE}/courses/{course.id}/progress")
    assert res.status_code == 200
    body = res.json()
    assert body["courseId"] == course.id
    assert body["completedLessons"] == 1
    assert body["totalLessons"] == 5
    assert body["requiredLessons"] == 4
    assert body["completedRequiredLessons"] == 1
    assert body["progressPercent"] == 20
    assert body["lastLessonId"] == lessons["Welcome & Kit"].id
    assert body["liveAttendancePercent"] is None
    assert body["assessmentPassed"] is False
    assert body["completionEligible"] is False
    assert body["isCompleted"] is False
    assert body["completedAt"] is None
    assert len(body["lessons"]) == 1
    row = body["lessons"][0]
    assert set(row) == {
        "id",
        "studentId",
        "courseId",
        "lessonId",
        "completed",
        "completedAt",
        "lastPositionSeconds",
        "updatedAt",
    }
    assert row["studentId"] == student.id


def test_progress_requires_enrollment(other_student_client, student_client, enrollment, course):
    assert other_student_client.get(f"{BASE}/courses/{course.id}/progress").status_code == 403
    assert student_client.get(f"{BASE}/courses/nope/progress").status_code == 404


def test_update_lesson_progress(student_client, enrollment, student, course, lessons):
    lesson = lessons["Welcome & Kit"]
    url = f"{BASE}/courses/{course.id}/lessons/{lesson.id}/progress"
    res = student_client.put(url, {"lastPositionSeconds": 120}, format="json")
    assert res.status_code == 200, res.content
    record = LessonProgress.objects.get(student=student, lesson=lesson)
    assert record.last_position_seconds == 120
    assert record.completed is False
    res = student_client.put(url, {"completed": True}, format="json")
    assert res.status_code == 200
    body = res.json()
    assert body["completedLessons"] == 1
    assert body["lessons"][0]["completed"] is True
    assert body["lessons"][0]["completedAt"] is not None
    assert body["lessons"][0]["lastPositionSeconds"] == 120
    # Un-complete.
    res = student_client.put(url, {"completed": False}, format="json")
    assert res.json()["completedLessons"] == 0
    assert LessonProgress.objects.filter(student=student, lesson=lesson).count() == 1


def test_update_live_lesson_forbidden(student_client, enrollment, course, lessons):
    url = f"{BASE}/courses/{course.id}/lessons/{lessons['Live Practical: Base Work'].id}/progress"
    res = student_client.put(url, {"completed": True}, format="json")
    assert res.status_code == 403
    assert res.json()["code"] == "NOT_PERMITTED"
    assert LessonProgress.objects.count() == 0


def test_update_lesson_progress_errors(
    student_client, other_student_client, admin_client, api, enrollment, course, recorded_course, lessons
):
    lesson = lessons["Welcome & Kit"]
    url = f"{BASE}/courses/{course.id}/lessons/{lesson.id}/progress"
    res = student_client.put(url, {"lastPositionSeconds": -5}, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
    assert "lastPositionSeconds" in res.json()["details"]
    assert (
        student_client.put(
            f"{BASE}/courses/{course.id}/lessons/les_nope/progress", {"completed": True}, format="json"
        ).status_code
        == 404
    )
    assert other_student_client.put(url, {"completed": True}, format="json").status_code == 403
    assert admin_client.put(url, {"completed": True}, format="json").status_code == 403
    assert api.put(url, {"completed": True}, format="json").status_code == 401
    # Lesson belonging to another course is not found under this course.
    from apps.courses.models import Lesson

    foreign = Lesson.objects.filter(module__course=recorded_course).first()
    assert (
        student_client.put(
            f"{BASE}/courses/{course.id}/lessons/{foreign.id}/progress", {"completed": True}, format="json"
        ).status_code
        == 404
    )


def test_update_progress_triggers_completion(
    student_client,
    enrollment,
    batch,
    student,
    course,
    lessons,
    complete_lesson,
    completed_session_factory,
    issue_certificate_mock,
):
    for title in ("Welcome & Kit", "Colour Theory", "Live Practical: Base Work"):
        complete_lesson(student, course, lessons[title])
    completed_session_factory(batch=batch, student=student)
    url = f"{BASE}/courses/{course.id}/lessons/{lessons['Final Assessment'].id}/progress"
    body = student_client.put(url, {"completed": True}, format="json").json()
    assert body["completionEligible"] is True
    assert body["isCompleted"] is True
    assert body["completedAt"] is not None
    issue_certificate_mock.assert_called_once()
    assert issue_certificate_mock.call_args.kwargs == {"trusted": True}
