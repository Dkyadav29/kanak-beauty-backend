"""Business rules for hero slides. Views stay thin."""

from __future__ import annotations

import secrets
from pathlib import PurePosixPath

from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Max
from django.urls import reverse

from apps.content.models import HeroSlide
from apps.core.services import log_audit
from common.exceptions import ValidationFailed
from common.validators import validate_image_upload

HERO_FOLDER = "hero-slides"
# Hero images are full-width banners, so they get a larger budget than thumbnails.
HERO_MAX_BYTES = 5_000_000

_CONTENT_TYPE_EXT = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/avif": ".avif",
}

_FIELDS = ("alt", "eyebrow", "caption", "focus", "is_active", "sort_order")


def _extension(uploaded_file) -> str:
    ext = PurePosixPath(getattr(uploaded_file, "name", "") or "").suffix.lower()
    if ext == ".jpeg":
        ext = ".jpg"
    if ext in _CONTENT_TYPE_EXT.values():
        return ext
    return _CONTENT_TYPE_EXT.get((getattr(uploaded_file, "content_type", "") or "").lower(), ".img")


def _validate(uploaded_file) -> None:
    try:
        validate_image_upload(uploaded_file, max_bytes=HERO_MAX_BYTES)
    except DjangoValidationError as exc:
        message = exc.messages[0] if exc.messages else "Invalid image upload."
        raise ValidationFailed(message, details={"image": [message]})


def _store_image(slide: HeroSlide, uploaded_file) -> None:
    """Save the file under a fresh name (so browsers never show a cached old image) and drop the previous one."""
    stale = slide.image_key
    name = f"{HERO_FOLDER}/{slide.id}-{secrets.token_hex(4)}{_extension(uploaded_file)}"
    saved = default_storage.save(name, uploaded_file)
    slide.image_key = saved
    slide.image_url = reverse("media-hero-slide", args=[PurePosixPath(saved).name])
    if stale:
        transaction.on_commit(lambda: default_storage.delete(stale))


def _use_external_url(slide: HeroSlide, url: str) -> None:
    stale = slide.image_key
    slide.image_key = ""
    slide.image_url = url
    if stale:
        transaction.on_commit(lambda: default_storage.delete(stale))


def _label(slide: HeroSlide) -> str:
    return slide.eyebrow or slide.alt


@transaction.atomic
def create_hero_slide(*, data: dict, actor, request=None) -> HeroSlide:
    if data.get("image"):
        _validate(data["image"])
    slide = HeroSlide(**{k: data[k] for k in _FIELDS if k in data})
    if "sort_order" not in data:
        top = HeroSlide.objects.aggregate(top=Max("sort_order"))["top"]
        slide.sort_order = 0 if top is None else top + 1
    slide.image_url = data.get("image_url") or ""
    slide.save()
    if data.get("image"):
        _store_image(slide, data["image"])
        slide.save(update_fields=["image_key", "image_url", "updated_at"])
    log_audit(
        actor=actor,
        action="hero_slide.create",
        entity_type="hero_slide",
        entity_id=slide.id,
        details=f'Added hero slide "{_label(slide)}".',
        request=request,
    )
    return slide


@transaction.atomic
def update_hero_slide(*, slide: HeroSlide, data: dict, actor, request=None) -> HeroSlide:
    if data.get("image"):
        _validate(data["image"])
        _store_image(slide, data["image"])
    elif data.get("image_url"):
        _use_external_url(slide, data["image_url"])
    for key in _FIELDS:
        if key in data:
            setattr(slide, key, data[key])
    slide.save()
    log_audit(
        actor=actor,
        action="hero_slide.update",
        entity_type="hero_slide",
        entity_id=slide.id,
        details=f'Updated hero slide "{_label(slide)}".',
        request=request,
    )
    return slide


@transaction.atomic
def delete_hero_slide(*, slide: HeroSlide, actor, request=None) -> None:
    stale, slide_id, label = slide.image_key, slide.id, _label(slide)
    slide.delete()
    if stale:
        transaction.on_commit(lambda: default_storage.delete(stale))
    log_audit(
        actor=actor,
        action="hero_slide.delete",
        entity_type="hero_slide",
        entity_id=slide_id,
        details=f'Removed hero slide "{label}".',
        request=request,
    )


@transaction.atomic
def reorder_hero_slides(*, ids: list[str], actor, request=None) -> list[HeroSlide]:
    slides = {s.id: s for s in HeroSlide.objects.select_for_update()}
    if len(ids) != len(set(ids)) or set(ids) != set(slides):
        raise ValidationFailed(
            "The order must list every hero slide exactly once.",
            details={"ids": ["Must list every hero slide exactly once."]},
        )
    ordered = []
    for position, slide_id in enumerate(ids):
        slide = slides[slide_id]
        slide.sort_order = position
        ordered.append(slide)
    HeroSlide.objects.bulk_update(ordered, ["sort_order"])
    log_audit(
        actor=actor,
        action="hero_slide.reorder",
        entity_type="hero_slide",
        entity_id="all",
        details=f"Reordered {len(ordered)} hero slides.",
        request=request,
    )
    return ordered
