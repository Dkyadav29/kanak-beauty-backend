"""Business rules for courses, curriculum and trainers. Views stay thin."""

from __future__ import annotations

import os
from pathlib import PurePosixPath

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import F

from apps.certificates.models import Certificate
from apps.core.services import log_audit
from apps.courses.models import Course, CourseModule, Lesson, Trainer
from apps.enrollments.models import CourseEnrollment, LessonProgress
from common.exceptions import ConflictError, ValidationFailed
from common.validators import validate_image_upload

# Orders are shifted out of the way before being reassigned so that (course, order)/(module, order)
# uniqueness never trips during a reorder. The constraints are declared DEFERRED, but neither
# MariaDB nor SQLite honour deferrable unique constraints, so the shift is what makes it safe.
_ORDER_SHIFT = 10_000

_CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}


# ---------------------------------------------------------------------------
# Courses
# ---------------------------------------------------------------------------


def _course_has_lessons(course: Course) -> bool:
    return Lesson.objects.filter(module__course=course).exists()


def _ensure_publishable(course: Course) -> None:
    if not _course_has_lessons(course):
        raise ConflictError("Add at least one lesson before publishing.", code="EMPTY_CURRICULUM")


def _apply_course_data(course: Course, data: dict, *, existing: bool) -> Course:
    criteria = data.get("completion_criteria")
    if criteria is None:
        criteria = course.completion_criteria if existing else {}
    course.title = data["title"]
    course.short_description = data["short_description"]
    course.description = data["description"]
    course.thumbnail_url = data.get("thumbnail_url") or (course.thumbnail_url if existing else "")
    course.trainer_id = data.get("trainer_id") or None
    course.duration_weeks = data["duration_weeks"]
    course.total_hours = data["total_hours"]
    course.level = data["level"]
    course.format = data["format"]
    course.price = data["price"]
    course.discount_price = data.get("discount_price")
    course.status = data.get("status") or (course.status if existing else "draft")
    course.featured = bool(data.get("featured", False))
    course.certificate_enabled = bool(data.get("certificate_enabled", True))
    course.certificate_issuance = data.get("certificate_issuance") or (
        course.certificate_issuance if existing else "automatic"
    )
    course.require_all_required_lessons = criteria.get("require_all_required_lessons", True)
    course.min_live_attendance_percent = criteria.get("min_live_attendance_percent")
    course.require_assessment = criteria.get("require_assessment", False)
    course.prerequisites = data.get("prerequisites", [])
    course.outcomes = data.get("outcomes", [])
    course.schedule_summary = data.get("schedule_summary") or ""
    return course


@transaction.atomic
def create_course(*, data: dict, actor, request=None) -> Course:
    course = _apply_course_data(Course(), data, existing=False)
    if course.status == "published":
        # A brand-new course has no lessons; publishing is blocked exactly like the publish endpoint.
        raise ConflictError("Add at least one lesson before publishing.", code="EMPTY_CURRICULUM")
    course.save()
    log_audit(
        actor=actor,
        action="course.create",
        entity_type="course",
        entity_id=course.id,
        details=f'Created course "{course.title}" ({course.status}).',
        request=request,
    )
    return course


@transaction.atomic
def update_course(*, course: Course, data: dict, actor, request=None) -> Course:
    was_published = course.status == "published"
    _apply_course_data(course, data, existing=True)
    if course.status == "published" and not was_published:
        _ensure_publishable(course)
    course.save()
    log_audit(
        actor=actor,
        action="course.update",
        entity_type="course",
        entity_id=course.id,
        details=f'Updated course "{course.title}".',
        request=request,
    )
    return course


@transaction.atomic
def publish_course(*, course: Course, actor, request=None) -> Course:
    _ensure_publishable(course)
    course.status = "published"
    course.save(update_fields=["status", "updated_at"])
    log_audit(
        actor=actor,
        action="course.publish",
        entity_type="course",
        entity_id=course.id,
        details=f'Published "{course.title}".',
        request=request,
    )
    return course


@transaction.atomic
def unpublish_course(*, course: Course, actor, request=None) -> Course:
    course.status = "unpublished"
    course.save(update_fields=["status", "updated_at"])
    log_audit(
        actor=actor,
        action="course.unpublish",
        entity_type="course",
        entity_id=course.id,
        details=f'Unpublished "{course.title}".',
        request=request,
    )
    return course


# ---------------------------------------------------------------------------
# Curriculum
# ---------------------------------------------------------------------------


@transaction.atomic
def replace_curriculum(*, course: Course, modules: list[dict], actor, request=None) -> Course:
    """
    Replace the curriculum structure while preserving the ids the client sent back.

    Modules/lessons whose ids belong to this course are updated in place (so LessonProgress and
    LiveSession/Recording links survive); entries without a known id are created; rows that are
    not present any more are deleted (LessonProgress cascades).
    """
    existing_modules = {m.id: m for m in CourseModule.objects.filter(course=course)}
    existing_lessons = {lesson.id: lesson for lesson in Lesson.objects.filter(module__course=course)}

    # Phase 1: move every current order out of the target range.
    CourseModule.objects.filter(course=course).update(order=F("order") + _ORDER_SHIFT)
    Lesson.objects.filter(module__course=course).update(order=F("order") + _ORDER_SHIFT)

    kept_module_ids: set[str] = set()
    kept_lesson_ids: set[str] = set()
    lesson_total = 0

    # Phase 2: upsert in the order supplied.
    for module_index, module_data in enumerate(modules, start=1):
        module = existing_modules.get(module_data.get("id") or "")
        if module is None:
            module = CourseModule(course=course)
        module.title = module_data["title"]
        module.description = module_data.get("description") or ""
        module.order = module_index
        module.save()
        kept_module_ids.add(module.id)

        for lesson_index, lesson_data in enumerate(module_data.get("lessons") or [], start=1):
            lesson = existing_lessons.get(lesson_data.get("id") or "")
            if lesson is None:
                lesson = Lesson(module=module)
            lesson.module = module
            lesson.title = lesson_data["title"]
            lesson.type = lesson_data.get("type") or "video"
            lesson.duration_minutes = lesson_data.get("duration_minutes") or 10
            lesson.is_required = lesson_data.get("is_required", True)
            lesson.summary = lesson_data.get("summary") or ""
            lesson.order = lesson_index
            lesson.save()
            kept_lesson_ids.add(lesson.id)
            lesson_total += 1

    # Phase 3: delete what the client dropped.
    removed_lessons = Lesson.objects.filter(module__course=course).exclude(id__in=kept_lesson_ids)
    removed_lesson_ids = list(removed_lessons.values_list("id", flat=True))
    progress_rows_lost = (
        LessonProgress.objects.filter(lesson_id__in=removed_lesson_ids).count() if removed_lesson_ids else 0
    )
    removed_lessons.delete()
    CourseModule.objects.filter(course=course).exclude(id__in=kept_module_ids).delete()

    details = f'Updated curriculum for "{course.title}" ({len(kept_module_ids)} modules, {lesson_total} lessons).'
    if removed_lesson_ids:
        details += f" Removed {len(removed_lesson_ids)} lesson(s)"
        details += f" affecting {progress_rows_lost} progress record(s)." if progress_rows_lost else "."
    log_audit(
        actor=actor,
        action="course.curriculum",
        entity_type="course",
        entity_id=course.id,
        details=details,
        request=request,
    )
    course.save(update_fields=["updated_at"])
    return course


# ---------------------------------------------------------------------------
# Thumbnail
# ---------------------------------------------------------------------------


def _thumbnail_extension(uploaded_file) -> str:
    ext = PurePosixPath(getattr(uploaded_file, "name", "") or "").suffix.lower()
    if ext in _CONTENT_TYPE_EXT.values():
        return ext
    return _CONTENT_TYPE_EXT.get((getattr(uploaded_file, "content_type", "") or "").lower(), ".img")


@transaction.atomic
def store_course_thumbnail(*, course: Course, uploaded_file, actor, request=None) -> str:
    try:
        validate_image_upload(uploaded_file)
    except DjangoValidationError as exc:
        message = exc.messages[0] if exc.messages else "Invalid image upload."
        raise ValidationFailed(message, details={"file": [message]})

    name = f"course-thumbnails/{course.id}{_thumbnail_extension(uploaded_file)}"
    # Replace any previous thumbnail for this course rather than letting the storage add a suffix.
    for stale in _existing_thumbnails(course):
        default_storage.delete(stale)
    saved_name = default_storage.save(name, uploaded_file)
    url = default_storage.url(saved_name)

    course.thumbnail_url = url
    course.save(update_fields=["thumbnail_url", "updated_at"])
    log_audit(
        actor=actor,
        action="course.update",
        entity_type="course",
        entity_id=course.id,
        details=f'Uploaded a new thumbnail for "{course.title}".',
        request=request,
    )
    return url


def _existing_thumbnails(course: Course) -> list[str]:
    folder = "course-thumbnails"
    try:
        _dirs, files = default_storage.listdir(folder)
    except (FileNotFoundError, NotADirectoryError, OSError):
        return []
    return [f"{folder}/{f}" for f in files if os.path.splitext(f)[0] == course.id]


# ---------------------------------------------------------------------------
# Enrolled students / stats
# ---------------------------------------------------------------------------


def enrolled_students(course: Course) -> list[dict]:
    rows = (
        CourseEnrollment.objects.filter(course=course, access_status="active")
        .select_related("student")
        .order_by("student__name")
    )
    return [
        {"student_id": e.student_id, "name": e.student.name, "email": e.student.email, "batch_id": e.batch_id}
        for e in rows
    ]


def academy_stats() -> dict:
    return {
        "courses_offered": Course.objects.filter(status="published").count(),
        "enrolled_students": CourseEnrollment.objects.filter(access_status="active")
        .values("student_id")
        .distinct()
        .count(),
        "expert_trainers": Trainer.objects.filter(is_active=True).count(),
        "certificates_issued": Certificate.objects.filter(status="issued").count(),
        "courses_completed": CourseEnrollment.objects.filter(completed_at__isnull=False).count(),
        "is_demo_data": False,
    }


# ---------------------------------------------------------------------------
# Trainers
# ---------------------------------------------------------------------------


def _apply_trainer_data(trainer: Trainer, data: dict) -> Trainer:
    trainer.name = data["name"]
    trainer.title = data["title"]
    trainer.bio = data.get("bio") or ""
    trainer.avatar_url = data.get("avatar_url") or ""
    trainer.specialities = data.get("specialities") or []
    return trainer


@transaction.atomic
def create_trainer(*, data: dict, actor, request=None) -> Trainer:
    trainer = _apply_trainer_data(Trainer(), data)
    trainer.save()
    log_audit(
        actor=actor,
        action="trainer.create",
        entity_type="trainer",
        entity_id=trainer.id,
        details=f'Created trainer "{trainer.name}".',
        request=request,
    )
    return trainer


@transaction.atomic
def update_trainer(*, trainer: Trainer, data: dict, actor, request=None) -> Trainer:
    _apply_trainer_data(trainer, data)
    trainer.save()
    log_audit(
        actor=actor,
        action="trainer.update",
        entity_type="trainer",
        entity_id=trainer.id,
        details=f'Updated trainer "{trainer.name}".',
        request=request,
    )
    return trainer
