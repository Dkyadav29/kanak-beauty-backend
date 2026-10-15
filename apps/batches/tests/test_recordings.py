from datetime import timedelta

import pytest
from django.core import signing
from django.utils import timezone

from apps.batches.models import LiveSession, Recording
from apps.batches.services import RECORDING_TOKEN_SALT
from apps.courses.models import Lesson
from apps.enrollments.models import LessonProgress

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1/student/recordings"


def playback_url(recording_id):
    return f"/api/v1/student/recordings/{recording_id}/playback"


@pytest.fixture
def recording(completed_session_with_recording):
    return completed_session_with_recording.recording


@pytest.fixture
def processing_recording(course, batch, trainer):
    session = LiveSession.objects.create(
        course=course,
        batch=batch,
        title="Processing one",
        trainer=trainer,
        starts_at=timezone.now() - timedelta(days=3),
        duration_minutes=90,
        status="completed",
    )
    return Recording.objects.create(
        session=session,
        course=course,
        title=session.title,
        recorded_at=session.starts_at,
        duration_minutes=90,
        status="processing",
    )


# --- list -------------------------------------------------------------------------


def test_recordings_list_shape_and_visibility(
    student_client, other_student_client, enrollment, recording, processing_recording, course, trainer
):
    res = student_client.get(LIST_URL)
    assert res.status_code == 200
    rows = res.json()
    assert [r["id"] for r in rows] == [processing_recording.id, recording.id]  # newest first
    row = rows[1]
    assert row == {
        "id": recording.id,
        "sessionId": recording.session_id,
        "courseId": course.id,
        "courseTitle": course.title,
        "title": recording.title,
        "trainerName": trainer.name,
        "recordedAt": row["recordedAt"],
        "durationMinutes": 120,
        "status": "available",
        "lessonId": None,
    }
    assert "storageKey" not in row
    assert other_student_client.get(LIST_URL).json() == []


def test_recordings_list_filters(student_client, enrollment, recording, processing_recording, recorded_course):
    assert len(student_client.get(LIST_URL, {"courseId": recording.course_id}).json()) == 2
    assert student_client.get(LIST_URL, {"courseId": recorded_course.id}).json() == []
    since = (timezone.now() - timedelta(days=7)).date().isoformat()
    assert [r["id"] for r in student_client.get(LIST_URL, {"from": since}).json()] == [processing_recording.id]
    until = (timezone.now() - timedelta(days=7)).date().isoformat()
    assert [r["id"] for r in student_client.get(LIST_URL, {"to": until}).json()] == [recording.id]
    assert student_client.get(LIST_URL, {"from": "not-a-date"}).status_code == 400


def test_recordings_list_requires_student(api, admin_client):
    assert api.get(LIST_URL).status_code == 401
    assert admin_client.get(LIST_URL).status_code == 403


# --- playback ----------------------------------------------------------------------


def test_playback_returns_signed_stream_url(student_client, student, enrollment, recording, settings):
    settings.SIGNED_URL_TTL_MINUTES = 30
    res = student_client.post(playback_url(recording.id))
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["streamUrl"].startswith("/api/v1/media/recordings/")
    assert body["resumePositionSeconds"] == 0
    token = body["streamUrl"].rsplit("/", 1)[1]
    assert signing.loads(token, salt=RECORDING_TOKEN_SALT) == {"rid": recording.id, "uid": student.id}
    assert "expiresAt" in body


def test_playback_resume_position_from_lesson_progress(student_client, student, enrollment, recording, course):
    lesson = Lesson.objects.filter(module__course=course, type="live").first()
    recording.lesson = lesson
    recording.save()
    LessonProgress.objects.create(
        student=student, course=course, lesson=lesson, completed=False, last_position_seconds=742
    )
    res = student_client.post(playback_url(recording.id))
    assert res.json()["resumePositionSeconds"] == 742


def test_playback_not_entitled_is_404(other_student_client, recording):
    assert other_student_client.post(playback_url(recording.id)).status_code == 404
    assert other_student_client.post(playback_url("rec_nope")).status_code == 404


def test_playback_unavailable_is_409(student_client, enrollment, processing_recording):
    res = student_client.post(playback_url(processing_recording.id))
    assert res.status_code == 409
    assert res.json()["code"] == "RECORDING_UNAVAILABLE"


def test_playback_requires_auth(api, recording):
    assert api.post(playback_url(recording.id)).status_code == 401


# --- media view -----------------------------------------------------------------------


def test_media_view_serves_file_with_valid_token(api, student_client, enrollment, recording, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    target = tmp_path / "recordings" / "eye-look.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"\x00\x00\x00\x18ftypmp42fake-video-bytes")
    stream_url = student_client.post(playback_url(recording.id)).json()["streamUrl"]

    res = api.get(stream_url)
    assert res.status_code == 200
    assert res["Content-Type"] == "video/mp4"
    assert b"".join(res.streaming_content) == target.read_bytes()


def test_media_view_404_when_file_missing(api, student_client, enrollment, recording, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    stream_url = student_client.post(playback_url(recording.id)).json()["streamUrl"]
    assert api.get(stream_url).status_code == 404


def test_media_view_rejects_tampered_token(api, student_client, enrollment, recording):
    stream_url = student_client.post(playback_url(recording.id)).json()["streamUrl"]
    res = api.get(stream_url[:-3] + "xyz")
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_TOKEN"


def test_media_view_rejects_expired_token(api, student, recording, settings):
    from datetime import UTC, datetime
    from unittest import mock

    settings.SIGNED_URL_TTL_MINUTES = 30
    with mock.patch("django.core.signing.time.time", return_value=datetime(2020, 1, 1, tzinfo=UTC).timestamp()):
        token = signing.dumps({"rid": recording.id, "uid": student.id}, salt=RECORDING_TOKEN_SALT)
    res = api.get(f"/api/v1/media/recordings/{token}")
    assert res.status_code == 410
    assert res.json()["code"] == "TOKEN_EXPIRED"


def test_media_view_rejects_path_traversal(api, student, recording, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path / "media"
    (tmp_path / "media").mkdir()
    (tmp_path / "secret.txt").write_text("nope")
    recording.storage_key = "../secret.txt"
    recording.save()
    token = signing.dumps({"rid": recording.id, "uid": student.id}, salt=RECORDING_TOKEN_SALT)
    assert api.get(f"/api/v1/media/recordings/{token}").status_code == 404
