"""Admin course management: list/detail/create/update/publish/unpublish/thumbnail/enrolled-students."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

pytestmark = pytest.mark.django_db

ADMIN = "/api/v1/admin/courses"


def course_payload(trainer, **overrides) -> dict:
    payload = {
        "title": "Advanced Hair Styling",
        "shortDescription": "Editorial and bridal hair for professionals.",
        "description": "A six-week intensive covering blow-dry technique, updos and editorial styling for shoots.",
        "thumbnailUrl": "https://cdn.test/hair.jpg",
        "trainerId": trainer.id,
        "durationWeeks": 6,
        "totalHours": 30,
        "level": "advanced",
        "format": "live",
        "price": 19999,
        "discountPrice": 17999,
        "status": "draft",
        "featured": False,
        "certificateEnabled": True,
        "certificateIssuance": "manual_approval",
        "completionCriteria": {
            "requireAllRequiredLessons": True,
            "minLiveAttendancePercent": 80,
            "requireAssessment": False,
        },
        "prerequisites": ["Basic hair knowledge", ""],
        "outcomes": ["Create bridal updos"],
        "scheduleSummary": "Weekends, 10:00-13:00",
    }
    payload.update(overrides)
    return payload


def audit_rows(action: str, entity_id: str):
    from apps.core.models import AuditLog

    return AuditLog.objects.filter(action=action, entity_type="course", entity_id=entity_id)


# ---------------------------------------------------------------------------
# List / detail
# ---------------------------------------------------------------------------


def test_admin_list_returns_all_statuses_newest_first(admin_client, course, recorded_course, draft_course):
    draft_course.title = "Airbrush Intensive (edited)"
    draft_course.save()
    res = admin_client.get(ADMIN)
    assert res.status_code == 200
    body = res.json()
    assert [c["id"] for c in body][0] == draft_course.id
    assert {c["id"] for c in body} == {course.id, recorded_course.id, draft_course.id}
    assert {c["status"] for c in body} == {"published", "draft"}
    assert all("modules" not in c and "moduleCount" in c for c in body)


def test_admin_list_filters(admin_client, course, recorded_course, draft_course):
    assert {c["id"] for c in admin_client.get(f"{ADMIN}?status=draft").json()} == {draft_course.id}
    assert {c["id"] for c in admin_client.get(f"{ADMIN}?status=all").json()} == {
        course.id,
        recorded_course.id,
        draft_course.id,
    }
    assert {c["id"] for c in admin_client.get(f"{ADMIN}?search=airbrush").json()} == {draft_course.id}
    assert {c["id"] for c in admin_client.get(f"{ADMIN}?search=meera").json()} == {
        course.id,
        recorded_course.id,
        draft_course.id,
    }
    # Admin search matches title/trainer only (not short description), like the mock.
    assert admin_client.get(f"{ADMIN}?search=pricing").json() == []


def test_admin_detail_includes_materials_without_storage_keys(admin_client, course, draft_course):
    from apps.courses.models import LearningMaterial, Lesson

    lesson = Lesson.objects.filter(module__course=course).first()
    mat = LearningMaterial.objects.create(
        lesson=lesson, title="Kit checklist", type="pdf", storage_key="materials/kit.pdf", size_label="1.2 MB"
    )
    res = admin_client.get(f"{ADMIN}/{course.id}")
    assert res.status_code == 200
    first = res.json()["modules"][0]["lessons"][0]
    assert first["materials"] == [{"id": mat.id, "title": "Kit checklist", "type": "pdf", "sizeLabel": "1.2 MB"}]
    assert "materials/kit.pdf" not in res.content.decode()
    # Drafts are visible to admins.
    assert admin_client.get(f"{ADMIN}/{draft_course.id}").status_code == 200
    assert admin_client.get(f"{ADMIN}/crs_missing").status_code == 404


# ---------------------------------------------------------------------------
# Create / update
# ---------------------------------------------------------------------------


def test_admin_create_course(admin_client, admin_user, trainer):
    res = admin_client.post(ADMIN, course_payload(trainer), format="json")
    assert res.status_code == 201, res.json()
    body = res.json()
    assert body["title"] == "Advanced Hair Styling"
    assert body["slug"] == "advanced-hair-styling"
    assert body["status"] == "draft"
    assert body["modules"] == []
    assert body["trainer"]["id"] == trainer.id
    assert body["price"] == 19999.0 and body["discountPrice"] == 17999.0
    assert body["currency"] == "INR"
    assert body["certificateIssuance"] == "manual_approval"
    assert body["completionCriteria"] == {
        "requireAllRequiredLessons": True,
        "minLiveAttendancePercent": 80,
        "requireAssessment": False,
    }
    assert body["prerequisites"] == ["Basic hair knowledge"]  # blanks dropped
    assert body["thumbnailUrl"] == "https://cdn.test/hair.jpg"
    row = audit_rows("course.create", body["id"]).get()
    assert row.actor_id == admin_user.id and "Advanced Hair Styling" in row.details


def test_admin_create_without_discount(admin_client, trainer):
    res = admin_client.post(ADMIN, course_payload(trainer, discountPrice=None), format="json")
    assert res.status_code == 201
    assert res.json()["discountPrice"] is None


@pytest.mark.parametrize("discount", [19999, 25000])
def test_admin_create_rejects_discount_not_below_price(admin_client, trainer, discount):
    res = admin_client.post(ADMIN, course_payload(trainer, discountPrice=discount), format="json")
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "VALIDATION"
    assert "discountPrice" in body["details"]
    from apps.courses.models import Course

    assert not Course.objects.filter(title="Advanced Hair Styling").exists()


def test_admin_create_rejects_unknown_trainer(admin_client, trainer):
    res = admin_client.post(ADMIN, course_payload(trainer, trainerId="tr_missing"), format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
    assert "trainerId" in res.json()["details"]


def test_admin_create_rejects_missing_required_fields(admin_client, trainer):
    res = admin_client.post(ADMIN, {"title": "x"}, format="json")
    assert res.status_code == 400
    details = res.json()["details"]
    assert {"shortDescription", "description", "price", "level", "format"} <= set(details)
    assert "trainerId" not in details  # trainer is optional


def test_admin_create_published_without_lessons_is_blocked(admin_client, trainer):
    res = admin_client.post(ADMIN, course_payload(trainer, status="published"), format="json")
    assert res.status_code == 409
    assert res.json()["code"] == "EMPTY_CURRICULUM"


def test_admin_update_course(admin_client, course, trainer):
    from apps.courses.models import Trainer

    new_trainer = Trainer.objects.create(name="Aarav Nair", title="Hair Educator")
    payload = course_payload(
        new_trainer,
        title="Professional Makeup Artistry 2.0",
        status="published",
        price=39999,
        discountPrice=None,
        featured=True,
    )
    res = admin_client.put(f"{ADMIN}/{course.id}", payload, format="json")
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body["id"] == course.id
    assert body["title"] == "Professional Makeup Artistry 2.0"
    assert body["slug"] == course.slug  # slug is stable across renames
    assert body["trainer"]["id"] == new_trainer.id
    assert body["price"] == 39999.0 and body["discountPrice"] is None
    assert body["featured"] is True
    assert len(body["modules"]) == 2  # curriculum untouched
    course.refresh_from_db()
    assert course.trainer_id == new_trainer.id and course.min_live_attendance_percent == 80
    assert audit_rows("course.update", course.id).count() == 1


def test_admin_update_validation_and_404(admin_client, course, trainer):
    res = admin_client.put(f"{ADMIN}/{course.id}", course_payload(trainer, price=100, discountPrice=100), format="json")
    assert res.status_code == 400 and "discountPrice" in res.json()["details"]
    course.refresh_from_db()
    assert course.price == 34999
    assert admin_client.put(f"{ADMIN}/crs_missing", course_payload(trainer), format="json").status_code == 404


def test_admin_update_to_published_requires_lessons(admin_client, draft_course, trainer):
    res = admin_client.put(f"{ADMIN}/{draft_course.id}", course_payload(trainer, status="published"), format="json")
    assert res.status_code == 409
    assert res.json()["code"] == "EMPTY_CURRICULUM"
    draft_course.refresh_from_db()
    assert draft_course.status == "draft"


# ---------------------------------------------------------------------------
# Publish / unpublish
# ---------------------------------------------------------------------------


def test_publish_blocked_on_empty_curriculum(admin_client, draft_course):
    res = admin_client.post(f"{ADMIN}/{draft_course.id}/publish")
    assert res.status_code == 409
    assert res.json() == {"code": "EMPTY_CURRICULUM", "message": "Add at least one lesson before publishing."}
    draft_course.refresh_from_db()
    assert draft_course.status == "draft"
    assert not audit_rows("course.publish", draft_course.id).exists()


def test_publish_with_lessons(api, admin_client, draft_course):
    from apps.courses.models import CourseModule, Lesson

    m = CourseModule.objects.create(course=draft_course, title="Intro", order=1)
    Lesson.objects.create(module=m, title="Welcome", order=1)
    res = admin_client.post(f"{ADMIN}/{draft_course.id}/publish")
    assert res.status_code == 200
    assert res.json()["status"] == "published"
    assert audit_rows("course.publish", draft_course.id).count() == 1
    assert api.get(f"/api/v1/courses/{draft_course.slug}").status_code == 200
    assert admin_client.post(f"{ADMIN}/crs_missing/publish").status_code == 404


def test_unpublish_hides_course_from_public_immediately(api, admin_client, course):
    assert api.get(f"/api/v1/courses/{course.id}").status_code == 200
    res = admin_client.post(f"{ADMIN}/{course.id}/unpublish")
    assert res.status_code == 200
    assert res.json()["status"] == "unpublished"
    assert api.get(f"/api/v1/courses/{course.id}").status_code == 404
    assert api.get(f"/api/v1/courses/{course.slug}/sessions").status_code == 404
    assert course.id not in {c["id"] for c in api.get("/api/v1/courses").json()}
    assert audit_rows("course.unpublish", course.id).count() == 1
    # Still visible to admins and re-publishable.
    assert admin_client.get(f"{ADMIN}/{course.id}").json()["status"] == "unpublished"
    assert admin_client.post(f"{ADMIN}/{course.id}/publish").json()["status"] == "published"


# ---------------------------------------------------------------------------
# Thumbnail upload
# ---------------------------------------------------------------------------


def test_thumbnail_upload_stores_file_and_url(admin_client, course, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    upload = SimpleUploadedFile("hero.PNG", b"\x89PNG\r\n\x1a\n" + b"0" * 128, content_type="image/png")
    res = admin_client.post(f"{ADMIN}/{course.id}/thumbnail", {"file": upload}, format="multipart")
    assert res.status_code == 200, res.content
    url = res.json()["url"]
    assert url == f"/media/course-thumbnails/{course.id}.png"
    assert (tmp_path / "course-thumbnails" / f"{course.id}.png").exists()
    course.refresh_from_db()
    assert course.thumbnail_url == url
    assert audit_rows("course.update", course.id).count() == 1

    # Re-uploading replaces the previous file instead of creating suffixed copies.
    upload2 = SimpleUploadedFile("hero2.jpg", b"\xff\xd8\xff" + b"1" * 64, content_type="image/jpeg")
    res2 = admin_client.post(f"{ADMIN}/{course.id}/thumbnail", {"file": upload2}, format="multipart")
    assert res2.status_code == 200
    assert res2.json()["url"] == f"/media/course-thumbnails/{course.id}.jpg"
    assert sorted(p.name for p in (tmp_path / "course-thumbnails").iterdir()) == [f"{course.id}.jpg"]


def test_thumbnail_upload_rejects_non_image(admin_client, course, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    upload = SimpleUploadedFile("notes.pdf", b"%PDF-1.4 ...", content_type="application/pdf")
    res = admin_client.post(f"{ADMIN}/{course.id}/thumbnail", {"file": upload}, format="multipart")
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
    assert "file" in res.json()["details"]
    assert not (tmp_path / "course-thumbnails").exists()
    course.refresh_from_db()
    assert course.thumbnail_url == "https://example.test/makeup.jpg"


def test_thumbnail_upload_rejects_large_file(admin_client, course, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    upload = SimpleUploadedFile("huge.png", b"0" * 1_600_000, content_type="image/png")
    res = admin_client.post(f"{ADMIN}/{course.id}/thumbnail", {"file": upload}, format="multipart")
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
    assert "smaller than" in res.json()["message"]


def test_thumbnail_upload_requires_file(admin_client, course):
    res = admin_client.post(f"{ADMIN}/{course.id}/thumbnail", {}, format="multipart")
    assert res.status_code == 400
    assert "file" in res.json()["details"]
    assert admin_client.post(f"{ADMIN}/crs_missing/thumbnail", {}, format="multipart").status_code == 404


# ---------------------------------------------------------------------------
# Enrolled students
# ---------------------------------------------------------------------------


def test_enrolled_students_lists_active_access_only(admin_client, course, enrollment, student, other_student, batch):
    from apps.accounts.models import User
    from apps.enrollments.models import CourseEnrollment

    CourseEnrollment.objects.create(
        student=other_student, course=course, source="admin_grant", access_status="active", batch=None
    )
    suspended = User.objects.create_user(
        email="sus@student.test", password="Student@123", name="Suspended", mobile="+91 90000 00001"
    )
    CourseEnrollment.objects.create(student=suspended, course=course, source="admin_grant", access_status="suspended")
    res = admin_client.get(f"{ADMIN}/{course.id}/enrolled-students")
    assert res.status_code == 200
    assert res.json() == [
        {"studentId": other_student.id, "name": "Ananya Verma", "email": "ananya@student.test", "batchId": None},
        {"studentId": student.id, "name": "Priya Sharma", "email": "priya@student.test", "batchId": batch.id},
    ]
    assert admin_client.get(f"{ADMIN}/crs_missing/enrolled-students").status_code == 404


def test_admin_create_course_without_trainer(admin_client, trainer):
    """Trainer is optional: blank/null trainerId creates the course with trainer=None."""
    for blank in ("", None):
        res = admin_client.post(ADMIN, course_payload(trainer, title=f"No trainer {blank!r}", trainerId=blank), format="json")
        assert res.status_code == 201, res.json()
        assert res.json()["trainer"] is None
    # Omitting the key entirely also works.
    payload = course_payload(trainer, title="No trainer key")
    payload.pop("trainerId")
    res = admin_client.post(ADMIN, payload, format="json")
    assert res.status_code == 201, res.json()
    assert res.json()["trainer"] is None
    # An unknown trainer id is still rejected.
    res = admin_client.post(ADMIN, course_payload(trainer, title="Bad trainer", trainerId="trn_missing"), format="json")
    assert res.status_code == 400
