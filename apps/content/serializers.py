"""Serializers for site content (hero slides)."""

from __future__ import annotations

from django.core.validators import URLValidator
from rest_framework import serializers

from apps.content.models import HeroSlide

_http_url = URLValidator(schemes=["http", "https"], message="Enter a valid http(s) image URL.")


class PublicHeroSlideSerializer(serializers.ModelSerializer):
    """What the home page needs. Inactive slides are never listed publicly."""

    class Meta:
        model = HeroSlide
        fields = ("id", "image_url", "alt", "eyebrow", "caption", "focus")
        read_only_fields = fields


class HeroSlideSerializer(serializers.ModelSerializer):
    """Admin view of a slide. ``imageUploaded`` tells an uploaded file from an external URL."""

    image_uploaded = serializers.SerializerMethodField()

    class Meta:
        model = HeroSlide
        fields = (
            "id",
            "image_url",
            "image_uploaded",
            "alt",
            "eyebrow",
            "caption",
            "focus",
            "sort_order",
            "is_active",
            "created_at",
            "updated_at",
        )
        read_only_fields = fields

    def get_image_uploaded(self, obj: HeroSlide) -> bool:
        return bool(obj.image_key)


class HeroSlideUpsertSerializer(serializers.Serializer):
    """
    Create (POST) or partially update (PATCH) a slide, as JSON or multipart.
    Supply either an uploaded ``image`` file or an external ``imageUrl``; on create one is required.
    """

    image = serializers.FileField(required=False)
    image_url = serializers.CharField(required=False, allow_blank=True)
    alt = serializers.CharField(min_length=2, max_length=200)
    eyebrow = serializers.CharField(required=False, allow_blank=True, max_length=60, default="")
    caption = serializers.CharField(required=False, allow_blank=True, max_length=160, default="")
    focus = serializers.ChoiceField(choices=[c[0] for c in HeroSlide.FOCUS_CHOICES], required=False, default="center")
    is_active = serializers.BooleanField(required=False, default=True)
    sort_order = serializers.IntegerField(required=False, min_value=0)

    def validate_image_url(self, value):
        value = (value or "").strip()
        if value:
            _http_url(value)
        return value

    def validate(self, attrs):
        if attrs.get("image") and attrs.get("image_url"):
            raise serializers.ValidationError({"image": ["Upload a file or give an image URL, not both."]})
        if not self.partial and not attrs.get("image") and not attrs.get("image_url"):
            raise serializers.ValidationError({"image": ["Upload an image or give an image URL."]})
        if self.partial and "image_url" in attrs and not attrs["image_url"] and not attrs.get("image"):
            raise serializers.ValidationError({"image_url": ["A slide must keep an image."]})
        return attrs


class HeroSlideOrderSerializer(serializers.Serializer):
    ids = serializers.ListField(child=serializers.CharField(max_length=40), allow_empty=False)
