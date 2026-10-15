from datetime import timedelta
from urllib.parse import parse_qs, urlparse

import pytest
from django.core import signing
from django.utils import timezone

from apps.batches.models import BatchEnrollment, LiveSession
from apps.batches.providers import JOIN_TICKET_SALT
from apps.batches.selectors import sessions_visible_to

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/student/live-sessions"


def join_url(session_id):
    return f"/api/v1/student/live-sessions/{session_id}/join"


# --- visibility -----------------------------------------------------------------


def test_non_enrolled_student_sees_nothing(other_student_client, joinable_session, course_wide_session):
    res = other_student_client.get(LIST_URL, {"scope": "all"})
    assert res.status_code == 200
    assert res.json() == []


def test_batch_bound_sessions_hidden_from_students_in_other_batches(
    other_student, other_enrollment, other_batch, joinable_session, course_wide_session
):
    BatchEnrollment.objects.create(batch=other_batch, student=other_student, status="enrolled")
    visible = set(sessions_visible_to(other_student).values_list("id", flat=True))
    assert visible == {course_wide_session.id}


def test_withdrawn_member_loses_batch_sessions(student, enrollment, joinable_session, course_wide_session):
    BatchEnrollment.objects.filter(student=student).update(status="withdrawn")
    visible = set(sessions_visible_to(student).values_list("id", flat=True))
    assert visible == {course_wide_session.id}


def test_suspended_enrollment_hides_everything(student, enrollment, joinable_session, course_wide_session):
    enrollment.access_status = "suspended"
    enrollment.save()
    assert not sessions_visible_to(student).exists()


def test_enrolled_student_list_has_join_flags(
    student_client, enrollment, joinable_session, future_session, course_wide_session
):
    res = student_client.get(LIST_URL)
    assert res.status_code == 200
    rows = {r["id"]: r for r in res.json()}
    assert list(rows) == [joinable_session.id, course_wide_session.id, future_session.id]
    j = rows[joinable_session.id]
    assert j["isEnrolled"] is True
    assert j["canJoinNow"] is True
    assert j["joinOpensAt"] and j["joinClosesAt"]
    f = rows[future_session.id]
    assert f["canJoinNow"] is False
    assert "providerMeetingRef" not in j


def test_scope_past_and_all(student_client, enrollment, joinable_session, completed_session_with_recording):
    past = student_client.get(LIST_URL, {"scope": "past"}).json()
    assert [r["id"] for r in past] == [completed_session_with_recording.id]
    assert past[0]["recordingId"] is not None
    everything = student_client.get(LIST_URL, {"scope": "all"}).json()
    assert [r["id"] for r in everything] == [completed_session_with_recording.id, joinable_session.id]
    assert student_client.get(LIST_URL, {"scope": "bogus"}).status_code == 400


def test_list_requires_student(api, admin_client, enrollment):
    assert api.get(LIST_URL).status_code == 401
    assert admin_client.get(LIST_URL).status_code == 403


# --- join -------------------------------------------------------------------------


def test_join_success_inside_window(student_client, student, enrollment, joinable_session, settings):
    settings.LIVE_PROVIDER = "internal"
    settings.PUBLIC_SITE_URL = "https://academy.test"
    res = student_client.post(join_url(joinable_session.id))
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["provider"] == "internal"
    assert body["joinUrl"].startswith(f"https://academy.test/student/live-classes/{joinable_session.id}/room?ticket=")
    ticket = parse_qs(urlparse(body["joinUrl"]).query)["ticket"][0]
    assert signing.loads(ticket, salt=JOIN_TICKET_SALT) == {"sid": joinable_session.id, "uid": student.id}
    assert "providerMeetingRef" not in body
    joinable_session.refresh_from_db()
    assert joinable_session.status == "live"
    # Joining again while live still works.
    assert student_client.post(join_url(joinable_session.id)).status_code == 200


def test_join_outside_window_is_403(student_client, enrollment, future_session):
    res = student_client.post(join_url(future_session.id))
    assert res.status_code == 403
    assert res.json()["code"] == "OUTSIDE_JOIN_WINDOW"
    future_session.refresh_from_db()
    assert future_session.status == "scheduled"


def test_join_not_entitled_is_404(other_student_client, joinable_session):
    res = other_student_client.post(join_url(joinable_session.id))
    assert res.status_code == 404


def test_join_other_batch_member_is_404(
    other_student_client, other_student, other_enrollment, other_batch, joinable_session
):
    BatchEnrollment.objects.create(batch=other_batch, student=other_student, status="enrolled")
    assert other_student_client.post(join_url(joinable_session.id)).status_code == 404


def test_join_cancelled_is_409(student_client, enrollment, joinable_session):
    joinable_session.status = "cancelled"
    joinable_session.save()
    res = student_client.post(join_url(joinable_session.id))
    assert res.status_code == 409
    assert res.json()["code"] == "SESSION_CANCELLED"


def test_join_completed_is_outside_window(student_client, enrollment, course, batch, trainer):
    session = LiveSession.objects.create(
        course=course,
        batch=batch,
        title="Done",
        trainer=trainer,
        starts_at=timezone.now() - timedelta(minutes=5),
        duration_minutes=60,
        status="completed",
    )
    res = student_client.post(join_url(session.id))
    assert res.status_code == 403
    assert res.json()["code"] == "OUTSIDE_JOIN_WINDOW"


def test_join_requires_auth_and_student_role(api, admin_client, joinable_session):
    assert api.post(join_url(joinable_session.id)).status_code == 401
    assert admin_client.post(join_url(joinable_session.id)).status_code == 403


def test_unknown_provider_is_loud(student_client, enrollment, joinable_session, settings):
    settings.LIVE_PROVIDER = "nope"
    res = student_client.post(join_url(joinable_session.id))
    assert res.status_code == 500
