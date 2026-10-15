from datetime import timedelta

import pytest
from django.utils import timezone

from apps.core.models import AuditLog
from apps.core.services import log_audit

pytestmark = pytest.mark.django_db

URL = "/api/v1/admin/audit-logs"


@pytest.fixture
def logs(admin_user):
    rows = [
        log_audit(
            actor=admin_user,
            action="course.publish",
            entity_type="course",
            entity_id="crs_1",
            details="Published Makeup",
        ),
        log_audit(
            actor=admin_user,
            action="batch.create",
            entity_type="batch",
            entity_id="bat_1",
            details="Created batch KBA-MK-03",
        ),
        log_audit(
            actor=None, action="payment.webhook", entity_type="payment", entity_id="pay_1", details="Marked paid"
        ),
    ]
    old = timezone.now() - timedelta(days=10)
    AuditLog.objects.filter(pk=rows[2].pk).update(at=old)
    rows[2].refresh_from_db()
    return rows


def test_audit_logs_admin_200_paginated(admin_client, logs):
    res = admin_client.get(URL)
    assert res.status_code == 200, res.content
    body = res.json()
    assert set(body) == {"items", "total", "page", "pageSize"}
    assert body["total"] == 3 and body["page"] == 1 and body["pageSize"] == 10
    item = body["items"][0]
    assert set(item) == {"id", "actorId", "actorName", "action", "entityType", "entityId", "details", "at"}
    # Newest first.
    assert [i["action"] for i in body["items"]] == ["batch.create", "course.publish", "payment.webhook"]
    system_row = body["items"][-1]
    assert system_row["actorId"] == "system" and system_row["actorName"] == "System"


def test_audit_logs_pagination_params(admin_client, logs):
    res = admin_client.get(URL, {"page": 2, "pageSize": 2})
    assert res.status_code == 200
    body = res.json()
    assert body["page"] == 2 and body["pageSize"] == 2 and body["total"] == 3
    assert len(body["items"]) == 1


def test_audit_logs_filters(admin_client, logs, admin_user):
    assert [i["action"] for i in admin_client.get(URL, {"entityType": "batch"}).json()["items"]] == ["batch.create"]
    assert admin_client.get(URL, {"search": "makeup"}).json()["total"] == 1  # details, case-insensitive
    assert admin_client.get(URL, {"search": "bat_1"}).json()["total"] == 1  # entity id
    assert admin_client.get(URL, {"search": admin_user.name}).json()["total"] == 2  # actor name
    today = timezone.localdate().isoformat()
    assert admin_client.get(URL, {"from": today}).json()["total"] == 2
    week_ago = (timezone.localdate() - timedelta(days=7)).isoformat()
    assert admin_client.get(URL, {"to": week_ago}).json()["total"] == 1
    assert admin_client.get(URL, {"from": week_ago, "to": today}).json()["total"] == 2
    iso_from = (timezone.now() - timedelta(minutes=5)).isoformat()
    assert admin_client.get(URL, {"from": iso_from}).json()["total"] == 2
    assert admin_client.get(URL, {"entityType": "all"}).json()["total"] == 3


def test_audit_logs_invalid_date_400(admin_client, logs):
    res = admin_client.get(URL, {"from": "not-a-date"})
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"


def test_audit_logs_student_403(student_client, logs):
    res = student_client.get(URL)
    assert res.status_code == 403
    assert res.json()["code"] == "FORBIDDEN"


def test_audit_logs_anonymous_401(api, logs):
    res = api.get(URL)
    assert res.status_code == 401
    assert res.json()["code"] == "UNAUTHENTICATED"
