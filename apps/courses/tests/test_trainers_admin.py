"""Admin trainer endpoints."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db

URL = "/api/v1/admin/trainers"


def test_admin_list_trainers_includes_inactive(admin_client, trainer):
    from apps.courses.models import Trainer

    inactive = Trainer.objects.create(name="Former Trainer", title="Left", is_active=False)
    res = admin_client.get(URL)
    assert res.status_code == 200
    assert {t["id"] for t in res.json()} == {trainer.id, inactive.id}


def test_admin_create_trainer(admin_client, admin_user):
    from apps.core.models import AuditLog

    payload = {
        "name": "Aarav Nair",
        "title": "Hair Educator",
        "bio": "Session stylist.",
        "avatarUrl": None,
        "specialities": ["Hair", " Colour ", ""],
    }
    res = admin_client.post(URL, payload, format="json")
    assert res.status_code == 201, res.json()
    body = res.json()
    assert body["id"].startswith("tr_")
    assert body["name"] == "Aarav Nair" and body["title"] == "Hair Educator" and body["bio"] == "Session stylist."
    assert body["avatarUrl"] is None
    assert body["specialities"] == ["Hair", "Colour"]
    row = AuditLog.objects.get(action="trainer.create", entity_type="trainer", entity_id=body["id"])
    assert row.actor_id == admin_user.id

    # Public list sees the new trainer.
    public = admin_client.get("/api/v1/trainers").json()
    assert body["id"] in {t["id"] for t in public}


def test_admin_create_trainer_validation(admin_client):
    res = admin_client.post(URL, {"name": "A"}, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
    assert {"name", "title"} <= set(res.json()["details"])


def test_admin_update_trainer(admin_client, trainer):
    from apps.core.models import AuditLog

    payload = {
        "name": "Meera Kapoor-Singh",
        "title": "Director of Education",
        "bio": "",
        "avatarUrl": "https://cdn.test/meera.jpg",
        "specialities": ["Bridal"],
    }
    res = admin_client.put(f"{URL}/{trainer.id}", payload, format="json")
    assert res.status_code == 200, res.json()
    body = res.json()
    assert body == {
        "id": trainer.id,
        "name": "Meera Kapoor-Singh",
        "title": "Director of Education",
        "bio": "",
        "avatarUrl": "https://cdn.test/meera.jpg",
        "specialities": ["Bridal"],
    }
    trainer.refresh_from_db()
    assert trainer.name == "Meera Kapoor-Singh" and trainer.avatar_url == "https://cdn.test/meera.jpg"
    assert AuditLog.objects.filter(action="trainer.update", entity_id=trainer.id).count() == 1
    assert admin_client.get(f"{URL}/{trainer.id}").json()["name"] == "Meera Kapoor-Singh"
    assert admin_client.put(f"{URL}/tr_missing", payload, format="json").status_code == 404
