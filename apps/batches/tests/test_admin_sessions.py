from datetime import timedelta

import pytest
from django.utils import timezone

from apps.batches.models import AttendanceRecord, BatchHistoryEvent, LiveSession, Recording
from apps.core.models import AuditLog
from apps.courses.models import Lesson
from apps.enrollments.models import LessonProgress

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/admin/live-sessions"


def cancel_url(session_id):
    return f"{LIST_URL}/{session_id}/cancel"


def attendance_url(session_id):
    return f"{LIST_URL}/{session_id}/attendance"


def recording_url(session_id):
    return f"{LIST_URL}/{session_id}/recording"


def session_payload(course, trainer, batch=None, **overrides):
    data = {
        "courseId": course.id,
        "batchId": batch.id if batch else None,
        "title": "Live Practical: Base Work",
        "trainerId": trainer.id,
        "startsAt": (timezone.now() + timedelta(days=3)).isoformat(),
        "durationMinutes": 90,
        "description": "Bring your kit.",
    }
    data.update(overrides)
    return data


# --- permissions ---------------------------------------------------------------------------


def test_admin_session_endpoints_reject_anonymous_and_students(api, student_client, joinable_session, course, trainer):
    for client, expected in ((api, 401), (student_client, 403)):
        assert client.get(LIST_URL).status_code == expected
        assert client.post(LIST_URL, session_payload(course, trainer), format="json").status_code == expected
        assert client.post(cancel_url(joinable_session.id)).status_code == expected
        assert client.post(attendance_url(joinable_session.id), {"records": []}, format="json").status_code == expected
        assert (
            client.post(recording_url(joinable_session.id), {"status": "processing"}, format="json").status_code
            == expected
        )


# --- list ------------------------------------------------------------------------------------


def test_admin_list_filters(
    admin_client,
    batch,
    other_batch,
    course,
    recorded_course,
    trainer,
    joinable_session,
    future_session,
    completed_session_with_recording,
):
    other = LiveSession.objects.create(
        course=recorded_course,
        title="Biz Q&A",
        trainer=trainer,
        starts_at=timezone.now() + timedelta(days=1),
        status="scheduled",
    )
    in_other_batch = LiveSession.objects.create(
        course=course,
        batch=other_batch,
        title="B4",
        trainer=trainer,
        starts_at=timezone.now() - timedelta(days=1),
        status="cancelled",
    )

    def ids(**params):
        res = admin_client.get(LIST_URL, params)
        assert res.status_code == 200, res.json()
        return [r["id"] for r in res.json()]

    assert ids() == [
        completed_session_with_recording.id,
        in_other_batch.id,
        joinable_session.id,
        other.id,
        future_session.id,
    ]
    assert ids(batchId=batch.id) == [completed_session_with_recording.id, joinable_session.id, future_session.id]
    assert ids(courseId=recorded_course.id) == [other.id]
    assert ids(scope="upcoming") == [joinable_session.id, other.id, future_session.id]
    assert ids(scope="upcoming", batchId=other_batch.id) == []
    row = admin_client.get(LIST_URL, {"batchId": batch.id}).json()[0]
    assert row["recordingId"] == completed_session_with_recording.recording.id
    assert "providerMeetingRef" not in row


# --- create ----------------------------------------------------------------------------------


def test_create_session_with_batch(admin_client, admin_user, course, trainer, batch):
    res = admin_client.post(
        LIST_URL, session_payload(course, trainer, batch, joinWindowMinutesBefore=30), format="json"
    )
    assert res.status_code == 201, res.json()
    body = res.json()
    assert body["courseId"] == course.id and body["batchId"] == batch.id and body["trainerId"] == trainer.id
    assert body["courseTitle"] == course.title and body["trainerName"] == trainer.name
    assert body["status"] == "scheduled" and body["durationMinutes"] == 90 and body["joinWindowMinutesBefore"] == 30
    assert body["recordingId"] is None and body["description"] == "Bring your kit."
    assert BatchHistoryEvent.objects.filter(batch=batch, type="session_scheduled", actor=admin_user).exists()
    audit = AuditLog.objects.get(action="session.create")
    assert audit.entity_type == "live_session" and audit.entity_id == body["id"]


def test_create_course_wide_session_defaults(admin_client, course, trainer, batch):
    data = session_payload(course, trainer)
    del data["durationMinutes"]
    del data["description"]
    res = admin_client.post(LIST_URL, data, format="json")
    assert res.status_code == 201, res.json()
    assert (
        res.json()["batchId"] is None
        and res.json()["durationMinutes"] == 60
        and res.json()["joinWindowMinutesBefore"] == 15
    )
    assert not BatchHistoryEvent.objects.filter(batch=batch).exists()


def test_create_session_batch_must_belong_to_course(admin_client, recorded_course, trainer, batch):
    res = admin_client.post(LIST_URL, session_payload(recorded_course, trainer, batch), format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
    assert "batchId" in res.json()["details"]


def test_create_session_validation(admin_client, course, trainer):
    res = admin_client.post(LIST_URL, session_payload(course, trainer, startsAt="not-a-date"), format="json")
    assert res.status_code == 400 and "startsAt" in res.json()["details"]
    res = admin_client.post(LIST_URL, session_payload(course, trainer, trainerId="tr_missing"), format="json")
    assert res.status_code == 400 and "trainerId" in res.json()["details"]
    res = admin_client.post(LIST_URL, session_payload(course, trainer, title="ab"), format="json")
    assert res.status_code == 400 and "title" in res.json()["details"]


# --- cancel ----------------------------------------------------------------------------------


def test_cancel_session(admin_client, admin_user, batch, future_session):
    res = admin_client.post(cancel_url(future_session.id))
    assert res.status_code == 200, res.json()
    assert res.json()["status"] == "cancelled"
    assert BatchHistoryEvent.objects.filter(batch=batch, type="session_cancelled", actor=admin_user).exists()
    assert AuditLog.objects.filter(action="session.cancel", entity_id=future_session.id).exists()
    assert admin_client.post(cancel_url("ses_missing")).status_code == 404


def test_cancel_completed_session_is_409(admin_client, completed_session_with_recording):
    res = admin_client.post(cancel_url(completed_session_with_recording.id))
    assert res.status_code == 409
    assert res.json()["code"] == "ALREADY_COMPLETED"


# --- attendance ------------------------------------------------------------------------------


def test_record_attendance_full_flow(
    admin_client,
    admin_user,
    batch,
    course,
    student,
    enrollment,
    other_student,
    other_enrollment,
    joinable_session,
    completion_spy,
):
    live_lesson = Lesson.objects.get(module__course=course, type="live")
    assert live_lesson.title == joinable_session.title  # matched by title (session.lesson unset)
    payload = {
        "records": [{"studentId": student.id, "status": "present"}, {"studentId": other_student.id, "status": "absent"}]
    }

    res = admin_client.post(attendance_url(joinable_session.id), payload, format="json")
    assert res.status_code == 200, res.json()
    assert res.json() == {"sessionId": joinable_session.id, "recorded": 2}

    joinable_session.refresh_from_db()
    assert joinable_session.status == "completed"
    records = {a.student_id: a for a in AttendanceRecord.objects.filter(session=joinable_session)}
    assert records[student.id].status == "present" and records[student.id].recorded_by_id == admin_user.id
    assert records[other_student.id].status == "absent"

    progress = LessonProgress.objects.get(student=student, lesson=live_lesson)
    assert progress.completed is True and progress.completed_at is not None and progress.course_id == course.id
    assert not LessonProgress.objects.filter(student=other_student, lesson=live_lesson).exists()

    # evaluate_completion called exactly once per student
    assert sorted(completion_spy) == sorted([(student.id, course.id), (other_student.id, course.id)])

    types = list(BatchHistoryEvent.objects.filter(batch=batch).order_by("at", "id").values_list("type", flat=True))
    assert types == ["attendance_recorded", "session_completed"]
    assert AuditLog.objects.filter(action="session.attendance", entity_id=joinable_session.id).exists()


def test_record_attendance_upserts_and_uses_explicit_lesson(
    admin_client, course, student, enrollment, future_session, completion_spy
):
    reading = Lesson.objects.get(module__course=course, title="Colour Theory")
    future_session.lesson = reading
    future_session.title = "Something unrelated"
    future_session.save()
    AttendanceRecord.objects.create(session=future_session, student=student, status="absent")

    res = admin_client.post(
        attendance_url(future_session.id), {"records": [{"studentId": student.id, "status": "late"}]}, format="json"
    )
    assert res.status_code == 200, res.json()
    assert AttendanceRecord.objects.filter(session=future_session).count() == 1
    assert AttendanceRecord.objects.get(session=future_session, student=student).status == "late"
    assert LessonProgress.objects.get(student=student, lesson=reading).completed is True
    assert completion_spy == [(student.id, course.id)]

    # recording again for the same session is still an upsert (session already completed)
    res = admin_client.post(
        attendance_url(future_session.id), {"records": [{"studentId": student.id, "status": "present"}]}, format="json"
    )
    assert res.status_code == 200
    assert AttendanceRecord.objects.filter(session=future_session).count() == 1


def test_record_attendance_cancelled_is_409(admin_client, student, joinable_session, completion_spy):
    joinable_session.status = "cancelled"
    joinable_session.save()
    res = admin_client.post(
        attendance_url(joinable_session.id),
        {"records": [{"studentId": student.id, "status": "present"}]},
        format="json",
    )
    assert res.status_code == 409
    assert res.json()["code"] == "CANCELLED"
    assert completion_spy == []


def test_record_attendance_validation(admin_client, student, joinable_session, completion_spy):
    assert admin_client.post(attendance_url(joinable_session.id), {"records": []}, format="json").status_code == 400
    assert (
        admin_client.post(
            attendance_url(joinable_session.id),
            {"records": [{"studentId": student.id, "status": "maybe"}]},
            format="json",
        ).status_code
        == 400
    )
    dup = {"records": [{"studentId": student.id, "status": "present"}, {"studentId": student.id, "status": "absent"}]}
    assert admin_client.post(attendance_url(joinable_session.id), dup, format="json").status_code == 400
    res = admin_client.post(
        attendance_url(joinable_session.id),
        {"records": [{"studentId": "usr_ghost", "status": "present"}]},
        format="json",
    )
    assert res.status_code == 400 and res.json()["code"] == "VALIDATION" and "records" in res.json()["details"]
    assert (
        admin_client.post(
            attendance_url("ses_missing"), {"records": [{"studentId": student.id, "status": "present"}]}, format="json"
        ).status_code
        == 404
    )
    joinable_session.refresh_from_db()
    assert joinable_session.status == "scheduled"
    assert completion_spy == []


def test_attendance_calls_real_contract_function_by_module_attribute(
    admin_client, student, enrollment, joinable_session, monkeypatch
):
    """Without a stub the contract function is what gets called (it may still be unimplemented)."""
    from apps.enrollments import services as enrollment_services

    called = []
    monkeypatch.setattr(enrollment_services, "evaluate_completion", lambda s, c: called.append(s.id) or True)
    admin_client.post(
        attendance_url(joinable_session.id),
        {"records": [{"studentId": student.id, "status": "present"}]},
        format="json",
    )
    assert called == [student.id]


# --- recording upsert --------------------------------------------------------------------------


def test_recording_create_and_update(admin_client, course, trainer, future_session):
    res = admin_client.post(recording_url(future_session.id), {"status": "processing"}, format="json")
    assert res.status_code == 201, res.json()
    body = res.json()
    assert body["sessionId"] == future_session.id and body["status"] == "processing"
    assert body["title"] == future_session.title and body["durationMinutes"] == future_session.duration_minutes
    assert body["courseTitle"] == course.title and body["trainerName"] == trainer.name
    assert "storageKey" not in body

    res = admin_client.post(
        recording_url(future_session.id),
        {"status": "available", "storageKey": "recordings/portfolio.mp4", "durationMinutes": 140},
        format="json",
    )
    assert res.status_code == 200, res.json()
    assert res.json()["status"] == "available" and res.json()["durationMinutes"] == 140
    rec = Recording.objects.get(session=future_session)
    assert rec.storage_key == "recordings/portfolio.mp4"
    assert Recording.objects.filter(session=future_session).count() == 1
    assert AuditLog.objects.filter(action="recording.update", entity_id=rec.id).count() == 2


def test_recording_available_requires_storage_key(admin_client, future_session):
    res = admin_client.post(recording_url(future_session.id), {"status": "available"}, format="json")
    assert res.status_code == 400
    assert "storageKey" in res.json()["details"]
