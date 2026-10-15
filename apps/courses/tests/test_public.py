"""Public catalogue endpoints: courses, sessions, trainers, academy stats."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

pytestmark = pytest.mark.django_db

COURSES = "/api/v1/courses"


# ---------------------------------------------------------------------------
# GET /courses
# ---------------------------------------------------------------------------


def test_list_returns_only_published_courses(api, course, recorded_course, draft_course, trainer):
    from apps.courses.models import Course

    Course.objects.create(
        title="Hidden Unpublished",
        short_description="Was published, now hidden.",
        description="This course has been unpublished and must not be listed publicly.",
        trainer=trainer,
        duration_weeks=1,
        total_hours=4,
        level="beginner",
        format="recorded",
        price=100,
        status="unpublished",
    )
    res = api.get(COURSES)
    assert res.status_code == 200
    ids = {c["id"] for c in res.json()}
    assert ids == {course.id, recorded_course.id}


def test_list_course_summary_shape(api, course):
    res = api.get(COURSES)
    row = next(c for c in res.json() if c["id"] == course.id)
    assert "modules" not in row
    assert row["moduleCount"] == 2
    assert row["lessonCount"] == 5
    assert row["slug"] == course.slug
    assert row["shortDescription"] == course.short_description
    assert row["price"] == 34999.0 and isinstance(row["price"], (int, float))
    assert row["discountPrice"] == 29999.0
    assert row["currency"] == "INR"
    assert row["trainer"]["id"] == course.trainer_id
    assert row["trainer"]["name"] == "Meera Kapoor"
    assert row["trainer"]["specialities"] == ["Bridal Makeup", "HD"]
    assert row["trainer"]["avatarUrl"] is None
    assert row["completionCriteria"] == {
        "requireAllRequiredLessons": True,
        "minLiveAttendancePercent": 75,
        "requireAssessment": True,
    }
    assert row["certificateIssuance"] == "automatic"
    assert row["prerequisites"] == ["None"]
    assert row["outcomes"] == ["Match foundation", "Build a portfolio"]
    assert row["scheduleSummary"] == "Live practicals twice a week."
    assert row["createdAt"] and row["updatedAt"]


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("search=makeup", {"course"}),
        ("search=SALON", {"recorded_course"}),
        ("search=Meera", {"course", "recorded_course"}),  # trainer name
        ("search=pricing", {"recorded_course"}),  # short description
        ("search=nothing-matches", set()),
        ("level=beginner", {"course"}),
        ("level=intermediate", {"recorded_course"}),
        ("level=all", {"course", "recorded_course"}),
        ("format=hybrid", {"course"}),
        ("format=recorded", {"recorded_course"}),
        ("format=live", set()),
        ("featured=true", {"course"}),
        ("featured=false", {"course", "recorded_course"}),
        ("level=beginner&format=recorded", set()),
    ],
)
def test_list_filters(api, course, recorded_course, draft_course, query, expected):
    lookup = {"course": course.id, "recorded_course": recorded_course.id}
    res = api.get(f"{COURSES}?{query}")
    assert res.status_code == 200
    assert {c["id"] for c in res.json()} == {lookup[k] for k in expected}


def test_list_is_a_constant_number_of_queries(api, course, recorded_course, trainer, django_assert_max_num_queries):
    from apps.courses.models import Course, CourseModule, Lesson

    for i in range(4):
        c = Course.objects.create(
            title=f"Extra course {i}",
            short_description="Another published course for the query count test.",
            description="Padding course so the list has several rows with trainers, modules and lessons.",
            trainer=trainer,
            duration_weeks=2,
            total_hours=10,
            level="advanced",
            format="live",
            price=1000,
            status="published",
        )
        m = CourseModule.objects.create(course=c, title="Only module", order=1)
        Lesson.objects.create(module=m, title="Only lesson", order=1)

    with django_assert_max_num_queries(2):
        res = api.get(COURSES)
    assert res.status_code == 200
    assert len(res.json()) == 6
    extra = next(c for c in res.json() if c["title"] == "Extra course 0")
    assert extra["moduleCount"] == 1 and extra["lessonCount"] == 1


# ---------------------------------------------------------------------------
# GET /courses/:idOrSlug
# ---------------------------------------------------------------------------


def test_detail_by_id_and_by_slug(api, course):
    by_id = api.get(f"{COURSES}/{course.id}")
    by_slug = api.get(f"{COURSES}/{course.slug}")
    assert by_id.status_code == 200 and by_slug.status_code == 200
    assert by_id.json()["id"] == by_slug.json()["id"] == course.id
    body = by_id.json()
    assert [m["title"] for m in body["modules"]] == ["Foundations", "Client-ready looks"]
    assert [lesson["order"] for lesson in body["modules"][0]["lessons"]] == [1, 2, 3]
    lesson = body["modules"][0]["lessons"][0]
    assert lesson["moduleId"] == body["modules"][0]["id"]
    assert body["modules"][0]["courseId"] == course.id
    assert set(lesson) == {
        "id",
        "moduleId",
        "title",
        "type",
        "durationMinutes",
        "order",
        "isRequired",
        "summary",
        "materials",
    }


def test_detail_strips_materials_and_content_fields(api, course):
    from apps.courses.models import LearningMaterial, Lesson

    lesson = Lesson.objects.filter(module__course=course).first()
    lesson.video_storage_key = "videos/secret.mp4"
    lesson.content = "private notes"
    lesson.save()
    LearningMaterial.objects.create(
        lesson=lesson, title="Kit checklist", type="pdf", storage_key="materials/kit.pdf", size_label="1.2 MB"
    )

    res = api.get(f"{COURSES}/{course.slug}")
    assert res.status_code == 200
    raw = res.content.decode()
    assert "secret.mp4" not in raw and "private notes" not in raw and "materials/kit.pdf" not in raw
    first = res.json()["modules"][0]["lessons"][0]
    assert first["materials"] == []
    assert "videoStorageKey" not in first and "content" not in first and "storageKey" not in raw


def test_detail_404_for_draft_or_unpublished_or_unknown(api, draft_course, course):
    assert api.get(f"{COURSES}/{draft_course.id}").status_code == 404
    assert api.get(f"{COURSES}/{draft_course.slug}").status_code == 404
    course.status = "unpublished"
    course.save()
    res = api.get(f"{COURSES}/{course.id}")
    assert res.status_code == 404
    assert res.json()["code"] == "NOT_FOUND"
    assert api.get(f"{COURSES}/does-not-exist").status_code == 404


# ---------------------------------------------------------------------------
# GET /courses/:idOrSlug/sessions
# ---------------------------------------------------------------------------


def test_sessions_lists_upcoming_scheduled_only(
    api, course, batch, trainer, joinable_session, future_session, completed_session_with_recording
):
    from apps.batches.models import LiveSession, Recording

    LiveSession.objects.create(
        course=course,
        batch=batch,
        title="Cancelled",
        trainer=trainer,
        starts_at=timezone.now() + timedelta(days=2),
        status="cancelled",
    )
    LiveSession.objects.create(
        course=course,
        batch=batch,
        title="Long ago",
        trainer=trainer,
        starts_at=timezone.now() - timedelta(days=3),
        status="scheduled",
    )
    with_recording = LiveSession.objects.create(
        course=course,
        batch=None,
        title="Scheduled with recording",
        trainer=trainer,
        starts_at=timezone.now() + timedelta(days=3),
        status="scheduled",
        provider_meeting_ref="zoom://secret-123",
    )
    rec = Recording.objects.create(
        session=with_recording,
        course=course,
        title="Replay",
        recorded_at=timezone.now(),
        status="processing",
        storage_key="rec/secret.mp4",
    )

    res = api.get(f"{COURSES}/{course.slug}/sessions")
    assert res.status_code == 200
    body = res.json()
    assert [s["id"] for s in body] == [joinable_session.id, with_recording.id, future_session.id]
    first = body[0]
    assert first == {
        "id": joinable_session.id,
        "courseId": course.id,
        "courseTitle": course.title,
        "batchId": batch.id,
        "title": "Live Practical: Base Work",
        "trainerId": trainer.id,
        "trainerName": trainer.name,
        "startsAt": first["startsAt"],
        "durationMinutes": 120,
        "status": "scheduled",
        "joinWindowMinutesBefore": 15,
        "recordingId": None,
        "description": "Foundation matching on live models.",
    }
    assert body[1]["recordingId"] == rec.id
    assert body[1]["batchId"] is None
    raw = res.content.decode()
    assert "secret-123" not in raw and "providerMeetingRef" not in raw and "secret.mp4" not in raw


def test_sessions_404_for_unpublished_course(api, draft_course):
    assert api.get(f"{COURSES}/{draft_course.id}/sessions").status_code == 404


def test_sessions_by_id_empty_when_none(api, course):
    res = api.get(f"{COURSES}/{course.id}/sessions")
    assert res.status_code == 200 and res.json() == []


# ---------------------------------------------------------------------------
# GET /trainers
# ---------------------------------------------------------------------------


def test_trainers_lists_active_only(api, trainer):
    from apps.courses.models import Trainer

    Trainer.objects.create(name="Former Trainer", title="Left", is_active=False)
    Trainer.objects.create(
        name="Aarav Nair", title="Hair Educator", avatar_url="https://cdn.test/a.jpg", specialities=["Hair"]
    )
    res = api.get("/api/v1/trainers")
    assert res.status_code == 200
    body = res.json()
    assert [t["name"] for t in body] == ["Aarav Nair", "Meera Kapoor"]
    assert body[0]["avatarUrl"] == "https://cdn.test/a.jpg"
    assert set(body[1]) == {"id", "name", "title", "bio", "avatarUrl", "specialities"}
    assert "isActive" not in body[0]


# ---------------------------------------------------------------------------
# GET /academy/stats
# ---------------------------------------------------------------------------


def test_academy_stats_computed_from_records(
    api, course, recorded_course, draft_course, trainer, enrollment, other_student, student
):
    from apps.certificates.models import Certificate
    from apps.courses.models import Trainer
    from apps.enrollments.models import CourseEnrollment

    Trainer.objects.create(name="Inactive", title="x", is_active=False)
    # Second active enrollment for the same student (distinct students must be 2, not 3).
    CourseEnrollment.objects.create(
        student=student,
        course=recorded_course,
        source="admin_grant",
        access_status="active",
        completed_at=timezone.now(),
    )
    CourseEnrollment.objects.create(student=other_student, course=course, source="admin_grant", access_status="active")
    # Revoked/suspended enrollments do not count as enrolled students.
    from apps.accounts.models import User

    third = User.objects.create_user(
        email="third@student.test", password="Student@123", name="Third", mobile="+91 90000 00000"
    )
    CourseEnrollment.objects.create(
        student=third, course=course, source="admin_grant", access_status="revoked", completed_at=timezone.now()
    )
    Certificate.objects.create(
        certificate_number="KBA-2025-0001",
        student=student,
        student_name=student.name,
        course=recorded_course,
        course_title=recorded_course.title,
        completion_date=timezone.now(),
    )
    Certificate.objects.create(
        certificate_number="KBA-2025-0002",
        student=other_student,
        student_name="x",
        course=course,
        course_title=course.title,
        completion_date=timezone.now(),
        status="revoked",
    )

    res = api.get("/api/v1/academy/stats")
    assert res.status_code == 200
    assert res.json() == {
        "coursesOffered": 2,
        "enrolledStudents": 2,
        "expertTrainers": 1,
        "certificatesIssued": 1,
        "coursesCompleted": 2,
        "isDemoData": False,
    }
    assert "max-age" in res["Cache-Control"]


def test_academy_stats_empty_database(api):
    res = api.get("/api/v1/academy/stats")
    assert res.status_code == 200
    assert res.json() == {
        "coursesOffered": 0,
        "enrolledStudents": 0,
        "expertTrainers": 0,
        "certificatesIssued": 0,
        "coursesCompleted": 0,
        "isDemoData": False,
    }


def test_public_endpoints_ignore_invalid_bearer_tokens(api, course):
    api.credentials(HTTP_AUTHORIZATION="Bearer not-a-real-token")
    assert api.get(COURSES).status_code == 200
    assert api.get("/api/v1/trainers").status_code == 200
