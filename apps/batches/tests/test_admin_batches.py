import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.utils import timezone

from apps.batches import services
from apps.batches.models import Batch, BatchEnrollment, BatchHistoryEvent, LiveSession
from apps.certificates.models import Certificate
from apps.core.models import AuditLog
from apps.courses.models import Lesson
from apps.enrollments.models import CourseEnrollment, LessonProgress

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/admin/batches"


def detail_url(batch_id):
    return f"{LIST_URL}/{batch_id}"


def payload(course, trainer, **overrides):
    today = timezone.localdate()
    data = {
        "name": "Makeup Artistry - Evening",
        "courseId": course.id,
        "trainerId": trainer.id,
        "startDate": today.isoformat(),
        "endDate": (today + timedelta(days=45)).isoformat(),
        "capacity": 12,
        "scheduleNote": "Mon & Wed, 18:00-20:00",
    }
    data.update(overrides)
    return data


# --- permissions ----------------------------------------------------------------------


def test_admin_batch_endpoints_reject_anonymous_and_students(api, student_client, batch, course, trainer):
    for client, expected in ((api, 401), (student_client, 403)):
        assert client.get(LIST_URL).status_code == expected
        assert client.get(detail_url(batch.id)).status_code == expected
        assert client.post(LIST_URL, payload(course, trainer), format="json").status_code == expected
        assert client.put(detail_url(batch.id), payload(course, trainer), format="json").status_code == expected
        assert (
            client.post(f"{detail_url(batch.id)}/status", {"status": "completed"}, format="json").status_code
            == expected
        )
        assert (
            client.post(f"{detail_url(batch.id)}/students", {"studentId": "x"}, format="json").status_code == expected
        )
        assert client.post(f"{detail_url(batch.id)}/students/x/withdraw", {}, format="json").status_code == expected


# --- list -------------------------------------------------------------------------------


def test_list_annotations_and_pagination(
    admin_client,
    batch,
    enrollment,
    other_student,
    other_batch,
    joinable_session,
    future_session,
    django_assert_num_queries,
):
    BatchEnrollment.objects.create(batch=batch, student=other_student, status="withdrawn")
    for i in range(3):
        Batch.objects.create(
            code=f"KBA-ZZ-0{i}",
            name=f"Filler {i}",
            course=batch.course,
            trainer=batch.trainer,
            start_date=timezone.localdate() - timedelta(days=100 + i),
            end_date=timezone.localdate(),
            capacity=5,
            status="archived",
        )

    with django_assert_num_queries(3):  # auth user + count + page
        res = admin_client.get(LIST_URL, {"pageSize": 2, "page": 1})
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 5 and body["page"] == 1 and body["pageSize"] == 2 and len(body["items"]) == 2
    assert body["items"][0]["id"] == other_batch.id  # newest start date first
    row = body["items"][1]
    assert row["id"] == batch.id
    assert row["courseTitle"] == batch.course.title
    assert row["trainerName"] == batch.trainer.name
    assert row["totalStudents"] == 1
    assert row["inProgressStudents"] == 1
    assert row["completedStudents"] == 0
    assert row["withdrawnStudents"] == 1
    assert row["sessionCount"] == 2
    assert row["status"] == "active"


def test_list_filters(admin_client, batch, other_batch, recorded_course, trainer):
    other = Batch.objects.create(
        code="KBA-SM-01",
        name="Salon Biz",
        course=recorded_course,
        trainer=trainer,
        start_date=timezone.localdate() - timedelta(days=400),
        end_date=timezone.localdate() - timedelta(days=300),
        capacity=5,
        status="archived",
    )

    def ids(**params):
        res = admin_client.get(LIST_URL, params)
        assert res.status_code == 200, res.json()
        return {r["id"] for r in res.json()["items"]}

    assert ids() == {batch.id, other_batch.id, other.id}  # all statuses incl. archived
    assert ids(status="all") == {batch.id, other_batch.id, other.id}
    assert ids(status="archived") == {other.id}
    assert ids(courseId=recorded_course.id) == {other.id}
    assert ids(trainerId=trainer.id) == {batch.id, other_batch.id, other.id}
    assert ids(search="salon") == {other.id}
    assert ids(search="KBA-MK") == {batch.id, other_batch.id}
    assert ids(search=batch.id.lower()) == {batch.id}
    assert ids(startFrom=(timezone.localdate() - timedelta(days=31)).isoformat()) == {batch.id, other_batch.id}
    assert ids(startTo=(timezone.localdate() - timedelta(days=1)).isoformat()) == {batch.id, other.id}
    assert admin_client.get(LIST_URL, {"startFrom": "nope"}).status_code == 400
    assert admin_client.get(LIST_URL, {"status": "weird"}).status_code == 400


# --- create / update ---------------------------------------------------------------------


def test_create_batch_generates_code_and_writes_history_and_audit(admin_client, admin_user, course, trainer):
    res = admin_client.post(LIST_URL, payload(course, trainer), format="json")
    assert res.status_code == 201, res.json()
    body = res.json()
    assert body["code"] == "KBA-PM-01"  # Professional Makeup Artistry
    assert body["status"] == "scheduled"
    assert body["courseId"] == course.id and body["trainerId"] == trainer.id
    assert body["scheduleNote"] == "Mon & Wed, 18:00-20:00"
    assert body["createdAt"] and body["updatedAt"]
    batch = Batch.objects.get(pk=body["id"])
    assert list(batch.history.values_list("type", flat=True)) == ["batch_created"]
    audit = AuditLog.objects.get(action="batch.create")
    assert audit.entity_id == batch.id and audit.actor_id == admin_user.id and audit.entity_type == "batch"

    second = admin_client.post(LIST_URL, payload(course, trainer, name="Another"), format="json")
    assert second.json()["code"] == "KBA-PM-02"


def test_create_batch_code_skips_taken_codes(course):
    Batch.objects.create(
        code="KBA-PM-01",
        name="x",
        course=course,
        trainer=course.trainer,
        start_date=timezone.localdate(),
        end_date=timezone.localdate(),
        capacity=1,
    )
    Batch.objects.create(
        code="KBA-PM-03",
        name="y",
        course=course,
        trainer=course.trainer,
        start_date=timezone.localdate(),
        end_date=timezone.localdate(),
        capacity=1,
    )
    assert services.generate_batch_code(course) == "KBA-PM-04"


def test_create_batch_validation(admin_client, course, trainer):
    res = admin_client.post(LIST_URL, payload(course, trainer, endDate="2000-01-01"), format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
    assert "endDate" in res.json()["details"]
    res = admin_client.post(LIST_URL, payload(course, trainer, courseId="crs_missing"), format="json")
    assert res.status_code == 400 and "courseId" in res.json()["details"]
    res = admin_client.post(LIST_URL, payload(course, trainer, capacity=0), format="json")
    assert res.status_code == 400 and "capacity" in res.json()["details"]


def test_update_batch(admin_client, batch, course, trainer):
    res = admin_client.put(detail_url(batch.id), payload(course, trainer, name="Renamed", capacity=20), format="json")
    assert res.status_code == 200, res.json()
    assert res.json()["name"] == "Renamed" and res.json()["capacity"] == 20
    assert res.json()["code"] == batch.code
    assert BatchHistoryEvent.objects.filter(batch=batch, type="batch_updated").exists()
    assert AuditLog.objects.filter(action="batch.update", entity_id=batch.id).exists()
    assert admin_client.put(detail_url("bt_missing"), payload(course, trainer), format="json").status_code == 404


def test_update_archived_batch_is_409(admin_client, batch, course, trainer):
    batch.status = "archived"
    batch.save()
    res = admin_client.put(detail_url(batch.id), payload(course, trainer), format="json")
    assert res.status_code == 409
    assert res.json()["code"] == "ARCHIVED"


# --- status transitions ---------------------------------------------------------------


def status_url(batch_id):
    return f"{detail_url(batch_id)}/status"


def test_valid_transitions(admin_client, other_batch):
    res = admin_client.post(status_url(other_batch.id), {"status": "active"}, format="json")
    assert res.status_code == 200 and res.json()["status"] == "active"
    res = admin_client.post(
        status_url(other_batch.id), {"status": "completed", "reason": "Course finished"}, format="json"
    )
    assert res.status_code == 200 and res.json()["status"] == "completed"
    res = admin_client.post(status_url(other_batch.id), {"status": "archived"}, format="json")
    assert res.status_code == 200 and res.json()["status"] == "archived"
    events = list(
        BatchHistoryEvent.objects.filter(batch=other_batch, type="status_changed")
        .order_by("at", "id")
        .values_list("description", flat=True)
    )
    assert events[0] == "Status changed from scheduled to active."
    assert events[1] == "Status changed from active to completed. Reason: Course finished"
    assert AuditLog.objects.filter(action="batch.status", entity_id=other_batch.id).count() == 2
    assert AuditLog.objects.filter(action="batch.archive", entity_id=other_batch.id).count() == 1


def test_invalid_transition_is_409(admin_client, batch):
    res = admin_client.post(status_url(batch.id), {"status": "archived"}, format="json")
    assert res.status_code == 409
    assert res.json()["code"] == "INVALID_TRANSITION"
    res = admin_client.post(status_url(batch.id), {"status": "scheduled"}, format="json")
    assert res.status_code == 409
    assert admin_client.post(status_url(batch.id), {"status": "nonsense"}, format="json").status_code == 400
    batch.refresh_from_db()
    assert batch.status == "active"


def test_cancel_cascades_to_scheduled_sessions(
    admin_client, batch, joinable_session, future_session, completed_session_with_recording
):
    res = admin_client.post(
        status_url(batch.id), {"status": "cancelled", "reason": "Trainer unavailable"}, format="json"
    )
    assert res.status_code == 200
    statuses = dict(LiveSession.objects.filter(batch=batch).values_list("id", "status"))
    assert statuses[joinable_session.id] == "cancelled"
    assert statuses[future_session.id] == "cancelled"
    assert statuses[completed_session_with_recording.id] == "completed"
    assert Batch.objects.filter(pk=batch.id).exists()  # nothing is deleted


# --- add student ------------------------------------------------------------------------


def students_url(batch_id):
    return f"{detail_url(batch_id)}/students"


def test_add_student_success(admin_client, admin_user, batch, other_student, other_enrollment):
    res = admin_client.post(students_url(batch.id), {"studentId": other_student.id}, format="json")
    assert res.status_code == 201, res.json()
    body = res.json()
    assert body["batchId"] == batch.id and body["studentId"] == other_student.id and body["status"] == "enrolled"
    assert body["completedAt"] is None and body["withdrawnAt"] is None
    other_enrollment.refresh_from_db()
    assert other_enrollment.batch_id == batch.id
    assert BatchHistoryEvent.objects.filter(batch=batch, type="student_added", actor=admin_user).exists()
    assert AuditLog.objects.filter(action="batch.add_student", entity_id=batch.id).exists()


def test_add_student_409_cases(
    admin_client, batch, other_batch, student, enrollment, other_student, other_enrollment, unverified_student
):
    # DUPLICATE: already a member
    res = admin_client.post(students_url(batch.id), {"studentId": student.id}, format="json")
    assert res.status_code == 409 and res.json()["code"] == "DUPLICATE"
    # NOT_ELIGIBLE: no active enrollment for the course
    res = admin_client.post(students_url(batch.id), {"studentId": unverified_student.id}, format="json")
    assert res.status_code == 409 and res.json()["code"] == "NOT_ELIGIBLE"
    # FULL: capacity reached
    batch.capacity = 1
    batch.save()
    res = admin_client.post(students_url(batch.id), {"studentId": other_student.id}, format="json")
    assert res.status_code == 409 and res.json()["code"] == "FULL"
    # BATCH_CLOSED
    for closed in ("completed", "cancelled", "archived"):
        other_batch.status = closed
        other_batch.save()
        res = admin_client.post(students_url(other_batch.id), {"studentId": other_student.id}, format="json")
        assert res.status_code == 409 and res.json()["code"] == "BATCH_CLOSED"


def test_add_student_404s_and_validation(admin_client, admin_user, batch):
    assert admin_client.post(students_url(batch.id), {"studentId": "usr_missing"}, format="json").status_code == 404
    assert (
        admin_client.post(students_url(batch.id), {"studentId": admin_user.id}, format="json").status_code == 404
    )  # not a student
    assert admin_client.post(students_url("bt_missing"), {"studentId": "x"}, format="json").status_code == 404
    assert admin_client.post(students_url(batch.id), {}, format="json").status_code == 400


def test_re_adding_withdrawn_student_reactivates_membership(admin_client, batch, student, enrollment):
    membership = BatchEnrollment.objects.get(batch=batch, student=student)
    membership.status = "withdrawn"
    membership.withdrawn_at = timezone.now()
    membership.save()
    res = admin_client.post(students_url(batch.id), {"studentId": student.id}, format="json")
    assert res.status_code == 201, res.json()
    assert res.json()["id"] == membership.id
    assert res.json()["status"] == "enrolled" and res.json()["withdrawnAt"] is None
    assert BatchEnrollment.objects.filter(batch=batch, student=student).count() == 1


def test_add_student_locks_batch_row(admin_user, batch, other_student, other_enrollment, monkeypatch):
    """The capacity check runs after select_for_update() on the batch inside a transaction."""
    seen = {}
    original = Batch.objects.get_queryset().__class__.select_for_update

    def spy(self, *args, **kwargs):
        seen["in_atomic"] = connection.in_atomic_block
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Batch.objects.get_queryset().__class__, "select_for_update", spy)
    services.add_student_to_batch(batch.id, other_student, actor=admin_user)
    assert seen == {"in_atomic": True}


@pytest.mark.skipif(connection.vendor == "sqlite", reason="row locks need a real database")
def test_capacity_race_two_threads_only_one_wins(
    admin_user, batch, other_student, other_enrollment, student, enrollment, transactional_db
):
    """With capacity 1 already taken, two concurrent adds for the same seat: one FULL, none over capacity."""
    from apps.accounts.models import User
    from common.exceptions import ConflictError

    third = User.objects.create_user(
        email="third@student.test",
        password="Student@123",
        name="Third Student",
        mobile="+91 90000 00003",
        email_verified=True,
    )
    CourseEnrollment.objects.create(student=third, course=batch.course, source="admin_grant", access_status="active")
    batch.capacity = 2
    batch.save()
    results = []

    def worker(user):
        try:
            services.add_student_to_batch(batch.id, user, actor=admin_user)
            results.append("ok")
        except ConflictError as exc:
            results.append(exc.code)
        finally:
            connection.close()

    threads = [threading.Thread(target=worker, args=(u,)) for u in (other_student, third)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["FULL", "ok"]
    assert BatchEnrollment.objects.filter(batch=batch).exclude(status="withdrawn").count() == 2


# --- withdraw -----------------------------------------------------------------------------


def withdraw_url(batch_id, student_id):
    return f"{detail_url(batch_id)}/students/{student_id}/withdraw"


def test_withdraw_student(admin_client, batch, student, enrollment):
    res = admin_client.post(withdraw_url(batch.id, student.id), {"reason": "Moved city"}, format="json")
    assert res.status_code == 200, res.json()
    assert res.json()["status"] == "withdrawn" and res.json()["withdrawnAt"]
    enrollment.refresh_from_db()
    assert enrollment.batch_id is None
    event = BatchHistoryEvent.objects.get(batch=batch, type="student_withdrawn")
    assert "Moved city" in event.description
    assert AuditLog.objects.filter(action="batch.withdraw_student", entity_id=batch.id).exists()
    # second withdraw -> no longer a member
    assert admin_client.post(withdraw_url(batch.id, student.id), {}, format="json").status_code == 404


def test_withdraw_404_and_already_completed(admin_client, batch, student, enrollment, other_student):
    assert admin_client.post(withdraw_url(batch.id, other_student.id), {}, format="json").status_code == 404
    assert admin_client.post(withdraw_url("bt_missing", student.id), {}, format="json").status_code == 404
    BatchEnrollment.objects.filter(batch=batch, student=student).update(status="completed")
    res = admin_client.post(withdraw_url(batch.id, student.id), {}, format="json")
    assert res.status_code == 409 and res.json()["code"] == "ALREADY_COMPLETED"


def test_withdraw_keeps_enrollment_batch_when_pointing_elsewhere(admin_client, batch, other_batch, student, enrollment):
    enrollment.batch = other_batch
    enrollment.save()
    assert admin_client.post(withdraw_url(batch.id, student.id), {}, format="json").status_code == 200
    enrollment.refresh_from_db()
    assert enrollment.batch_id == other_batch.id


# --- detail -------------------------------------------------------------------------------


def test_batch_detail_contents(
    admin_client,
    admin_user,
    batch,
    student,
    enrollment,
    other_student,
    course,
    trainer,
    joinable_session,
    completed_session_with_recording,
):
    from apps.batches.models import AttendanceRecord

    # progress: 2 of 5 lessons completed -> 40%
    lessons = list(Lesson.objects.filter(module__course=course).order_by("module__order", "order"))
    for lesson in lessons[:2]:
        LessonProgress.objects.create(
            student=student, course=course, lesson=lesson, completed=True, completed_at=timezone.now()
        )
    # attendance: 1 completed batch session, student present -> 100%
    AttendanceRecord.objects.create(session=completed_session_with_recording, student=student, status="late")
    cert = Certificate.objects.create(
        certificate_number="KBA-2026-00201",
        student=student,
        student_name=student.name,
        course=course,
        course_title=course.title,
        batch=batch,
        completion_date=timezone.now(),
    )
    # withdrawn member with no progress
    BatchEnrollment.objects.create(batch=batch, student=other_student, status="withdrawn")
    # history: three events, newest first expected
    e1 = services.add_history(batch, "batch_created", "created", admin_user)
    e2 = services.add_history(batch, "student_added", "added", admin_user)
    e3 = services.add_history(batch, "status_changed", "changed", None)
    BatchHistoryEvent.objects.filter(pk=e1.pk).update(at=timezone.now() - timedelta(days=3))
    BatchHistoryEvent.objects.filter(pk=e2.pk).update(at=timezone.now() - timedelta(days=2))
    BatchHistoryEvent.objects.filter(pk=e3.pk).update(at=timezone.now() - timedelta(days=1))

    res = admin_client.get(detail_url(batch.id))
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["id"] == batch.id and body["code"] == batch.code
    assert body["totalStudents"] == 1 and body["withdrawnStudents"] == 1 and body["sessionCount"] == 2
    assert set(body["sessionIds"]) == {joinable_session.id, completed_session_with_recording.id}
    assert [h["id"] for h in body["history"]] == [e3.id, e2.id, e1.id]
    assert body["history"][0]["actorId"] is None and body["history"][0]["actorName"] == "System"
    assert body["history"][1]["actorId"] == admin_user.id and body["history"][1]["actorName"] == admin_user.name
    assert body["history"][1]["type"] == "student_added" and body["history"][1]["batchId"] == batch.id

    rows = {r["studentId"]: r for r in body["students"]}
    assert set(rows) == {student.id, other_student.id}
    me = rows[student.id]
    assert me["studentName"] == student.name and me["studentEmail"] == student.email
    assert me["progressPercent"] == 40
    assert me["attendancePercent"] == 100
    assert me["certificateNumber"] == cert.certificate_number
    assert me["enrollment"]["status"] == "in_progress" and me["enrollment"]["batchId"] == batch.id
    other = rows[other_student.id]
    assert other["progressPercent"] == 0 and other["attendancePercent"] == 0 and other["certificateNumber"] is None
    assert other["enrollment"]["status"] == "withdrawn"


def test_batch_detail_attendance_null_without_completed_sessions(admin_client, batch, enrollment, joinable_session):
    body = admin_client.get(detail_url(batch.id)).json()
    assert body["students"][0]["attendancePercent"] is None
    assert body["students"] and body["history"] == []


def test_batch_detail_available_for_archived_and_404_for_missing(admin_client, batch):
    batch.status = "archived"
    batch.save()
    assert admin_client.get(detail_url(batch.id)).status_code == 200
    assert admin_client.get(detail_url("bt_missing")).status_code == 404
