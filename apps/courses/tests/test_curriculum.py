"""PUT /admin/courses/:id/modules replaces the curriculum while preserving ids."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


def url(course) -> str:
    return f"/api/v1/admin/courses/{course.id}/modules"


def current_curriculum(course) -> list[dict]:
    """Serialise the stored curriculum into the request shape (ids included)."""
    return [
        {
            "id": m.id,
            "title": m.title,
            "description": m.description,
            "lessons": [
                {
                    "id": lesson.id,
                    "title": lesson.title,
                    "type": lesson.type,
                    "durationMinutes": lesson.duration_minutes,
                    "isRequired": lesson.is_required,
                    "summary": lesson.summary,
                }
                for lesson in m.lessons.all()
            ],
        }
        for m in course.modules.prefetch_related("lessons")
    ]


def test_replace_keeps_ids_reorders_and_deletes(admin_client, course, student, enrollment):
    from apps.courses.models import CourseModule, Lesson
    from apps.enrollments.models import LessonProgress

    modules = current_curriculum(course)
    m1, m2 = modules
    welcome, colour, live = m1["lessons"]
    assessment, bonus = m2["lessons"]
    LessonProgress.objects.create(student=student, course=course, lesson_id=welcome["id"], completed=True)
    LessonProgress.objects.create(student=student, course=course, lesson_id=bonus["id"], completed=True)

    payload = {
        "modules": [
            # Module 2 moves first and gains a brand-new lesson; its "bonus" lesson is dropped.
            {
                "id": m2["id"],
                "title": "Client-ready looks (updated)",
                "description": m2["description"],
                "lessons": [
                    {
                        "title": "Portfolio Shoot",
                        "type": "live",
                        "durationMinutes": 90,
                        "isRequired": True,
                        "summary": "Studio day",
                    },
                    assessment,
                ],
            },
            # Module 1 keeps its id; lessons are reversed and the live lesson moves to module 2's place.
            {
                "id": m1["id"],
                "title": m1["title"],
                "description": "Tools and prep (v2)",
                "lessons": [live, {**welcome, "title": "Welcome & Kit (v2)"}],
            },
            # A new module.
            {
                "title": "Aftercare",
                "description": "",
                "lessons": [
                    {
                        "title": "Skin aftercare",
                        "type": "reading",
                        "durationMinutes": 15,
                        "isRequired": False,
                        "summary": "",
                    }
                ],
            },
        ]
    }
    res = admin_client.put(url(course), payload, format="json")
    assert res.status_code == 200, res.json()
    body = res.json()

    assert [m["id"] for m in body["modules"][:2]] == [m2["id"], m1["id"]]
    assert [m["order"] for m in body["modules"]] == [1, 2, 3]
    assert body["modules"][0]["title"] == "Client-ready looks (updated)"
    assert body["modules"][1]["description"] == "Tools and prep (v2)"
    assert body["modules"][2]["title"] == "Aftercare" and body["modules"][2]["id"].startswith("mod_")

    first_lessons = body["modules"][0]["lessons"]
    assert [lesson["order"] for lesson in first_lessons] == [1, 2]
    assert first_lessons[0]["title"] == "Portfolio Shoot" and first_lessons[0]["id"].startswith("les_")
    assert first_lessons[1]["id"] == assessment["id"]

    second_lessons = body["modules"][1]["lessons"]
    assert [lesson["id"] for lesson in second_lessons] == [live["id"], welcome["id"]]
    assert [lesson["order"] for lesson in second_lessons] == [1, 2]
    assert second_lessons[1]["title"] == "Welcome & Kit (v2)"
    assert all(lesson["moduleId"] == m1["id"] for lesson in second_lessons)

    # Dropped rows are gone; kept rows and their progress survive.
    assert not Lesson.objects.filter(id__in=[colour["id"], bonus["id"]]).exists()
    assert Lesson.objects.filter(module__course=course).count() == 5
    assert CourseModule.objects.filter(course=course).count() == 3
    assert LessonProgress.objects.filter(lesson_id=welcome["id"], completed=True).exists()
    assert not LessonProgress.objects.filter(lesson_id=bonus["id"]).exists()

    from apps.core.models import AuditLog

    row = AuditLog.objects.get(action="course.curriculum", entity_id=course.id)
    assert "3 modules" in row.details and "Removed 2 lesson(s)" in row.details and "1 progress record(s)" in row.details


def test_replace_swapping_two_modules_respects_unique_order(admin_client, course):
    """Swapping orders must not trip the (course, order) / (module, order) uniqueness mid-transaction."""
    m1, m2 = current_curriculum(course)
    m1["lessons"] = list(reversed(m1["lessons"]))
    res = admin_client.put(url(course), {"modules": [m2, m1]}, format="json")
    assert res.status_code == 200, res.json()
    body = res.json()
    assert [m["id"] for m in body["modules"]] == [m2["id"], m1["id"]]
    assert [lesson["title"] for lesson in body["modules"][1]["lessons"]] == [
        "Live Practical: Base Work",
        "Colour Theory",
        "Welcome & Kit",
    ]
    assert [lesson["order"] for lesson in body["modules"][1]["lessons"]] == [1, 2, 3]


def test_replace_moves_lesson_between_modules(admin_client, course):
    m1, m2 = current_curriculum(course)
    moved = m1["lessons"].pop(0)
    m2["lessons"].insert(0, moved)
    res = admin_client.put(url(course), {"modules": [m1, m2]}, format="json")
    assert res.status_code == 200
    body = res.json()
    assert body["modules"][1]["lessons"][0]["id"] == moved["id"]
    assert body["modules"][1]["lessons"][0]["moduleId"] == m2["id"]
    assert len(body["modules"][0]["lessons"]) == 2 and len(body["modules"][1]["lessons"]) == 3


def test_replace_with_empty_list_clears_curriculum(admin_client, course):
    from apps.courses.models import CourseModule, Lesson

    res = admin_client.put(url(course), {"modules": []}, format="json")
    assert res.status_code == 200
    assert res.json()["modules"] == []
    assert CourseModule.objects.filter(course=course).count() == 0
    assert Lesson.objects.filter(module__course=course).count() == 0
    # ...after which publishing is blocked again.
    assert admin_client.post(f"/api/v1/admin/courses/{course.id}/publish").status_code == 409


def test_replace_ignores_ids_from_other_courses(admin_client, course, recorded_course):
    """An id that belongs to another course cannot be hijacked; a fresh row is created instead."""
    from apps.courses.models import Lesson

    foreign_lesson = Lesson.objects.filter(module__course=recorded_course).first()
    foreign_module = foreign_lesson.module
    payload = {
        "modules": [
            {
                "id": foreign_module.id,
                "title": "Stolen?",
                "description": "",
                "lessons": [
                    {
                        "id": foreign_lesson.id,
                        "title": "Mine now",
                        "type": "video",
                        "durationMinutes": 5,
                        "isRequired": True,
                        "summary": "",
                    }
                ],
            }
        ]
    }
    res = admin_client.put(url(course), payload, format="json")
    assert res.status_code == 200
    body = res.json()
    assert body["modules"][0]["id"] != foreign_module.id
    assert body["modules"][0]["lessons"][0]["id"] != foreign_lesson.id
    foreign_lesson.refresh_from_db()
    assert foreign_lesson.title == "Pricing Strategy" and foreign_lesson.module_id == foreign_module.id


def test_replace_applies_defaults_and_validates(admin_client, course):
    res = admin_client.put(
        url(course), {"modules": [{"title": "Minimal", "lessons": [{"title": "Lesson"}]}]}, format="json"
    )
    assert res.status_code == 200
    lesson = res.json()["modules"][0]["lessons"][0]
    assert (
        lesson["type"] == "video"
        and lesson["durationMinutes"] == 10
        and lesson["isRequired"] is True
        and lesson["summary"] == ""
    )

    bad = admin_client.put(
        url(course), {"modules": [{"title": "X", "lessons": [{"title": "ok", "type": "podcast"}]}]}, format="json"
    )
    assert bad.status_code == 400
    assert bad.json()["code"] == "VALIDATION"
    assert admin_client.put(url(course), {"modules": "nope"}, format="json").status_code == 400
    assert admin_client.put(url(course), {}, format="json").status_code == 400
    assert (
        admin_client.put("/api/v1/admin/courses/crs_missing/modules", {"modules": []}, format="json").status_code == 404
    )


def test_replace_is_atomic_on_failure(admin_client, course, monkeypatch):
    from apps.courses import services
    from apps.courses.models import Lesson

    before = sorted(Lesson.objects.filter(module__course=course).values_list("id", "order"))

    def boom(**kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(services, "log_audit", boom)
    res = admin_client.put(url(course), {"modules": []}, format="json")
    assert res.status_code == 500
    assert sorted(Lesson.objects.filter(module__course=course).values_list("id", "order")) == before
