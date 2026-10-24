"""Hero slider: public feed, admin management and the public image endpoint."""

from __future__ import annotations

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile

pytestmark = pytest.mark.django_db

PUBLIC = "/api/v1/hero-slides"
ADMIN = "/api/v1/admin/hero-slides"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def _media(settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path


def _png(name="banner.png", content_type="image/png", data=PNG):
    return SimpleUploadedFile(name, data, content_type=content_type)


@pytest.fixture
def slides(db):
    from apps.content.models import HeroSlide

    return [
        HeroSlide.objects.create(
            image_url="https://cdn.test/one.jpg", alt="Bridal look", eyebrow="Bridal", caption="One", sort_order=0
        ),
        HeroSlide.objects.create(
            image_url="https://cdn.test/two.jpg", alt="Hair styling", eyebrow="Hair", caption="Two", sort_order=1
        ),
        HeroSlide.objects.create(
            image_url="https://cdn.test/hidden.jpg", alt="Hidden slide", sort_order=2, is_active=False
        ),
    ]


# --------------------------------------------------------------------------- public


def test_public_lists_active_slides_in_order(api, slides):
    res = api.get(PUBLIC)
    assert res.status_code == 200
    body = res.json()
    assert [s["id"] for s in body] == [slides[0].id, slides[1].id]
    assert body[0] == {
        "id": slides[0].id,
        "imageUrl": "https://cdn.test/one.jpg",
        "alt": "Bridal look",
        "eyebrow": "Bridal",
        "caption": "One",
        "focus": "center",
    }


def test_public_empty(api):
    res = api.get(PUBLIC)
    assert res.status_code == 200 and res.json() == []


# --------------------------------------------------------------------------- permissions


def test_admin_requires_auth(api):
    assert api.get(ADMIN).status_code == 401
    assert api.post(ADMIN, {}, format="json").status_code == 401


def test_admin_forbidden_for_students(student_client, slides):
    assert student_client.get(ADMIN).status_code == 403
    assert (
        student_client.post(ADMIN, {"alt": "x", "imageUrl": "https://a.test/x.jpg"}, format="json").status_code == 403
    )
    assert student_client.patch(f"{ADMIN}/{slides[0].id}", {"alt": "Changed"}, format="json").status_code == 403
    assert student_client.delete(f"{ADMIN}/{slides[0].id}").status_code == 403


def test_admin_has_content_permission(admin_client):
    perms = admin_client.get("/api/v1/auth/me").json()["permissions"]
    assert "content:manage" in perms


# --------------------------------------------------------------------------- admin CRUD


def test_admin_lists_all_including_hidden(admin_client, slides):
    res = admin_client.get(ADMIN)
    assert res.status_code == 200
    assert [s["id"] for s in res.json()] == [s.id for s in slides]
    assert res.json()[2]["isActive"] is False


def test_admin_create_with_upload(admin_client, admin_user, api):
    from apps.content.models import HeroSlide
    from apps.core.models import AuditLog

    res = admin_client.post(
        ADMIN,
        {"image": _png(), "alt": "Studio practice", "eyebrow": "Studio", "caption": "Learn", "focus": "top"},
        format="multipart",
    )
    assert res.status_code == 201, res.json()
    body = res.json()
    assert body["id"].startswith("hero_")
    assert body["imageUploaded"] is True
    assert body["imageUrl"].startswith("/api/v1/media/hero-slides/")
    assert body["focus"] == "top" and body["isActive"] is True and body["sortOrder"] == 0
    assert AuditLog.objects.filter(action="hero_slide.create", entity_id=body["id"], actor=admin_user).exists()

    image = api.get(body["imageUrl"])
    assert image.status_code == 200
    assert image["Content-Type"] == "image/png"
    assert b"".join(image.streaming_content) == PNG

    # Shows up on the public feed.
    assert [s["id"] for s in api.get(PUBLIC).json()] == [body["id"]]
    assert HeroSlide.objects.get(pk=body["id"]).image_key.startswith("hero-slides/")


def test_admin_create_with_external_url_appends_to_end(admin_client, slides):
    res = admin_client.post(ADMIN, {"imageUrl": "https://cdn.test/new.jpg", "alt": "New slide"}, format="json")
    assert res.status_code == 201, res.json()
    body = res.json()
    assert body["imageUrl"] == "https://cdn.test/new.jpg"
    assert body["imageUploaded"] is False
    assert body["sortOrder"] == 3


def test_admin_create_multipart_booleans(admin_client):
    res = admin_client.post(ADMIN, {"image": _png(), "alt": "Draft slide", "isActive": "false"}, format="multipart")
    assert res.status_code == 201, res.json()
    assert res.json()["isActive"] is False


@pytest.mark.parametrize(
    "payload, field",
    [
        ({"alt": "No image here"}, "image"),
        ({"imageUrl": "https://cdn.test/a.jpg"}, "alt"),
        ({"imageUrl": "javascript:alert(1)", "alt": "Bad url"}, "imageUrl"),
        ({"imageUrl": "https://cdn.test/a.jpg", "alt": "Bad focus", "focus": "middle"}, "focus"),
    ],
)
def test_admin_create_validation(admin_client, payload, field):
    res = admin_client.post(ADMIN, payload, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
    assert field in res.json()["details"]


def test_admin_create_rejects_non_image(admin_client):
    res = admin_client.post(
        ADMIN, {"image": _png("notes.txt", "text/plain", b"hello"), "alt": "Not an image"}, format="multipart"
    )
    assert res.status_code == 400
    assert "image" in res.json()["details"]


def test_admin_create_rejects_oversized_image(admin_client):
    big = PNG + b"\x00" * 5_000_001
    res = admin_client.post(ADMIN, {"image": _png(data=big), "alt": "Too big"}, format="multipart")
    assert res.status_code == 400
    assert "image" in res.json()["details"]


def test_admin_update_fields(admin_client, slides):
    from apps.core.models import AuditLog

    res = admin_client.patch(f"{ADMIN}/{slides[0].id}", {"caption": "Updated", "isActive": False}, format="json")
    assert res.status_code == 200, res.json()
    assert res.json()["caption"] == "Updated" and res.json()["isActive"] is False
    assert res.json()["alt"] == "Bridal look"
    assert AuditLog.objects.filter(action="hero_slide.update", entity_id=slides[0].id).exists()


def test_admin_replace_uploaded_image_removes_old_file(admin_client, api, django_capture_on_commit_callbacks):
    from django.core.files.storage import default_storage

    from apps.content.models import HeroSlide

    created = admin_client.post(ADMIN, {"image": _png(), "alt": "Studio"}, format="multipart").json()
    old_key = HeroSlide.objects.get(pk=created["id"]).image_key

    with django_capture_on_commit_callbacks(execute=True):
        res = admin_client.patch(
            f"{ADMIN}/{created['id']}", {"image": _png("new.webp", "image/webp")}, format="multipart"
        )
    assert res.status_code == 200, res.json()
    assert res.json()["imageUrl"] != created["imageUrl"] and res.json()["imageUrl"].endswith(".webp")
    assert not default_storage.exists(old_key)
    assert api.get(created["imageUrl"]).status_code == 404

    # Switching to an external URL drops the uploaded file too.
    new_key = HeroSlide.objects.get(pk=created["id"]).image_key
    with django_capture_on_commit_callbacks(execute=True):
        res = admin_client.patch(f"{ADMIN}/{created['id']}", {"imageUrl": "https://cdn.test/x.jpg"}, format="json")
    assert res.json()["imageUploaded"] is False
    assert not default_storage.exists(new_key)


def test_admin_update_cannot_clear_image(admin_client, slides):
    res = admin_client.patch(f"{ADMIN}/{slides[0].id}", {"imageUrl": ""}, format="json")
    assert res.status_code == 400
    assert "imageUrl" in res.json()["details"]


def test_admin_update_unknown_slide(admin_client):
    assert admin_client.patch(f"{ADMIN}/hero_missing", {"alt": "Nope"}, format="json").status_code == 404


def test_admin_delete(admin_client, slides, api):
    from apps.content.models import HeroSlide
    from apps.core.models import AuditLog

    res = admin_client.delete(f"{ADMIN}/{slides[0].id}")
    assert res.status_code == 204
    assert not HeroSlide.objects.filter(pk=slides[0].id).exists()
    assert AuditLog.objects.filter(action="hero_slide.delete", entity_id=slides[0].id).exists()
    assert [s["id"] for s in api.get(PUBLIC).json()] == [slides[1].id]
    assert admin_client.delete(f"{ADMIN}/{slides[0].id}").status_code == 404


def test_admin_reorder(admin_client, slides, api):
    ids = [slides[2].id, slides[1].id, slides[0].id]
    res = admin_client.put(f"{ADMIN}/order", {"ids": ids}, format="json")
    assert res.status_code == 200, res.json()
    assert [s["id"] for s in res.json()] == ids
    assert [s["id"] for s in api.get(PUBLIC).json()] == [slides[1].id, slides[0].id]


def test_admin_reorder_must_list_every_slide(admin_client, slides):
    res = admin_client.put(f"{ADMIN}/order", {"ids": [slides[0].id, slides[1].id]}, format="json")
    assert res.status_code == 400
    res = admin_client.put(f"{ADMIN}/order", {"ids": [slides[0].id, slides[0].id, slides[1].id]}, format="json")
    assert res.status_code == 400


# --------------------------------------------------------------------------- image endpoint


def test_image_endpoint_only_serves_slide_files(api, settings, tmp_path):
    (tmp_path / "hero-slides").mkdir()
    (tmp_path / "hero-slides" / "orphan.png").write_bytes(PNG)
    assert api.get("/api/v1/media/hero-slides/orphan.png").status_code == 404
    assert api.get("/api/v1/media/hero-slides/..%2Frecordings%2Fx.mp4").status_code == 404
