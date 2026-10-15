from datetime import timedelta

import pytest
from django.utils import timezone

from apps.batches.models import LiveSession

pytestmark = pytest.mark.django_db

URL = "/api/v1/live-sessions/upcoming"


def test_public_upcoming_lists_scheduled_sessions_of_published_courses(
    api, joinable_session, future_session, draft_course, trainer, course, batch
):
    LiveSession.objects.create(
        course=draft_course,
        title="Draft only",
        trainer=trainer,
        starts_at=timezone.now() + timedelta(days=1),
        status="scheduled",
    )
    LiveSession.objects.create(
        course=course,
        batch=batch,
        title="Too far",
        trainer=trainer,
        starts_at=timezone.now() + timedelta(days=60),
        status="scheduled",
    )
    LiveSession.objects.create(
        course=course,
        batch=batch,
        title="Cancelled",
        trainer=trainer,
        starts_at=timezone.now() + timedelta(days=1),
        status="cancelled",
    )

    res = api.get(URL)
    assert res.status_code == 200
    ids = [row["id"] for row in res.json()]
    assert ids == [joinable_session.id, future_session.id]
    row = res.json()[0]
    assert row["courseTitle"] == course.title
    assert row["trainerName"] == trainer.name
    assert row["batchId"] == batch.id
    assert row["recordingId"] is None
    assert "providerMeetingRef" not in row and "provider_meeting_ref" not in row


def test_public_upcoming_respects_limit(api, joinable_session, future_session):
    res = api.get(URL, {"limit": 1})
    assert res.status_code == 200
    assert len(res.json()) == 1
    assert res.json()[0]["id"] == joinable_session.id


def test_public_upcoming_rejects_bad_limit(api):
    res = api.get(URL, {"limit": "abc"})
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
