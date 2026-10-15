"""Authorization: anonymous callers get 401, students get 403 on every admin endpoint."""

from __future__ import annotations

import pytest
from rest_framework.test import APIClient

pytestmark = pytest.mark.django_db

ADMIN_ENDPOINTS = [
    ("get", "/api/v1/admin/courses"),
    ("post", "/api/v1/admin/courses"),
    ("get", "/api/v1/admin/courses/{course}"),
    ("put", "/api/v1/admin/courses/{course}"),
    ("post", "/api/v1/admin/courses/{course}/publish"),
    ("post", "/api/v1/admin/courses/{course}/unpublish"),
    ("put", "/api/v1/admin/courses/{course}/modules"),
    ("post", "/api/v1/admin/courses/{course}/thumbnail"),
    ("get", "/api/v1/admin/courses/{course}/enrolled-students"),
    ("get", "/api/v1/admin/trainers"),
    ("post", "/api/v1/admin/trainers"),
    ("get", "/api/v1/admin/trainers/{trainer}"),
    ("put", "/api/v1/admin/trainers/{trainer}"),
]


def _call(client: APIClient, method: str, path: str):
    return getattr(client, method)(path, {}, format="json")


@pytest.mark.parametrize(("method", "template"), ADMIN_ENDPOINTS)
def test_anonymous_gets_401(api, course, trainer, method, template):
    res = _call(api, method, template.format(course=course.id, trainer=trainer.id))
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHENTICATED"


@pytest.mark.parametrize(("method", "template"), ADMIN_ENDPOINTS)
def test_student_gets_403(student_client, course, trainer, method, template):
    res = _call(student_client, method, template.format(course=course.id, trainer=trainer.id))
    assert res.status_code == 403
    assert res.json()["code"] in ("FORBIDDEN", "PERMISSION_DENIED")  # common.exceptions maps DRF PermissionDenied


def test_student_cannot_mutate_anything(student_client, course):
    from apps.core.models import AuditLog

    student_client.post(f"/api/v1/admin/courses/{course.id}/unpublish")
    course.refresh_from_db()
    assert course.status == "published"
    assert AuditLog.objects.count() == 0


def test_public_endpoints_need_no_auth(api, course):
    for path in [
        "/api/v1/courses",
        f"/api/v1/courses/{course.id}",
        f"/api/v1/courses/{course.id}/sessions",
        "/api/v1/trainers",
        "/api/v1/academy/stats",
    ]:
        assert api.get(path).status_code == 200, path
