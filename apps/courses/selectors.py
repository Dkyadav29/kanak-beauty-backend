"""Reusable querysets for the courses app (N+1 safe)."""

from __future__ import annotations

from datetime import timedelta

from django.db.models import Count, F, Q, QuerySet
from django.shortcuts import get_object_or_404
from django.utils import timezone

from apps.batches.models import LiveSession
from apps.courses.models import Course, Trainer


def course_summaries() -> QuerySet[Course]:
    """Courses with trainer joined and module/lesson counts annotated (one query for a list)."""
    return Course.objects.select_related("trainer").annotate(
        module_count=Count("modules", distinct=True),
        lesson_count=Count("modules__lessons", distinct=True),
    )


def course_with_curriculum() -> QuerySet[Course]:
    """Courses with the full curriculum prefetched for the detail serializer."""
    return Course.objects.select_related("trainer").prefetch_related("modules__lessons__materials")


def published_filter() -> Q:
    return Q(status="published")


def id_or_slug_filter(id_or_slug: str) -> Q:
    return Q(pk=id_or_slug) | Q(slug=id_or_slug)


def get_course_for_admin(course_id: str) -> Course:
    return get_object_or_404(course_with_curriculum(), pk=course_id)


def get_published_course(id_or_slug: str) -> Course:
    return get_object_or_404(course_with_curriculum().filter(published_filter()), id_or_slug_filter(id_or_slug))


def search_courses(qs: QuerySet[Course], search: str | None, *, include_description: bool) -> QuerySet[Course]:
    if not search:
        return qs
    q = Q(title__icontains=search) | Q(trainer__name__icontains=search)
    if include_description:
        q |= Q(short_description__icontains=search)
    return qs.filter(q)


def upcoming_sessions_for_course(course: Course) -> QuerySet[LiveSession]:
    """Scheduled sessions that have not started more than an hour ago (mirrors the mock)."""
    threshold = timezone.now() - timedelta(hours=1)
    return (
        LiveSession.objects.filter(course=course, status="scheduled", starts_at__gt=threshold)
        .select_related("course", "trainer")
        .annotate(recording_pk=F("recording__id"))
        .order_by("starts_at")
    )


def active_trainers() -> QuerySet[Trainer]:
    return Trainer.objects.filter(is_active=True)
