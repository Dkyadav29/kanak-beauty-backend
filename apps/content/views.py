"""Public hero slider feed, admin hero slide management and the public hero image endpoint."""

from __future__ import annotations

import mimetypes

from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle
from rest_framework.views import APIView

from apps.content import services
from apps.content.models import HeroSlide
from apps.content.serializers import (
    HeroSlideOrderSerializer,
    HeroSlideSerializer,
    HeroSlideUpsertSerializer,
    PublicHeroSlideSerializer,
)
from common.permissions import HasPermission

TAGS = ["content"]


class PublicHeroSlideListView(APIView):
    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [AnonRateThrottle]

    @extend_schema(
        tags=TAGS, summary="Active hero slides in display order", responses=PublicHeroSlideSerializer(many=True)
    )
    def get(self, request):
        slides = HeroSlide.objects.filter(is_active=True).order_by("sort_order", "created_at")
        response = Response(PublicHeroSlideSerializer(slides, many=True).data)
        response["Cache-Control"] = "public, max-age=60"
        return response


class HeroSlideImageView(APIView):
    """
    GET /media/hero-slides/{filename}

    Serves an uploaded hero image. Only files that belong to a slide are served,
    so nothing else under MEDIA_ROOT (recordings, thumbnails) is reachable here.
    """

    permission_classes = [AllowAny]
    authentication_classes: list = []

    @extend_schema(tags=TAGS, summary="Uploaded hero slide image", responses={(200, "image/*"): OpenApiTypes.BINARY})
    def get(self, request, filename: str):
        key = f"{services.HERO_FOLDER}/{filename}"
        if not HeroSlide.objects.filter(image_key=key).exists() or not default_storage.exists(key):
            raise Http404
        content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
        response = FileResponse(default_storage.open(key, "rb"), content_type=content_type)
        # File names are unique per upload, so the bytes behind a URL never change.
        response["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


class AdminHeroSlideView(APIView):
    permission_classes = [HasPermission]
    required_permission = "content:manage"


class AdminHeroSlideListCreateView(AdminHeroSlideView):
    @extend_schema(tags=TAGS, summary="List hero slides (incl. hidden)", responses=HeroSlideSerializer(many=True))
    def get(self, request):
        return Response(HeroSlideSerializer(HeroSlide.objects.all(), many=True).data)

    @extend_schema(
        tags=TAGS,
        summary="Add a hero slide (multipart with an image file, or JSON with imageUrl)",
        request={"multipart/form-data": HeroSlideUpsertSerializer, "application/json": HeroSlideUpsertSerializer},
        responses={201: HeroSlideSerializer},
    )
    def post(self, request):
        serializer = HeroSlideUpsertSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        slide = services.create_hero_slide(data=serializer.validated_data, actor=request.user, request=request)
        return Response(HeroSlideSerializer(slide).data, status=status.HTTP_201_CREATED)


class AdminHeroSlideOrderView(AdminHeroSlideView):
    @extend_schema(
        tags=TAGS,
        summary="Reorder hero slides (ids must list every slide once)",
        request=HeroSlideOrderSerializer,
        responses=HeroSlideSerializer(many=True),
    )
    def put(self, request):
        serializer = HeroSlideOrderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        slides = services.reorder_hero_slides(ids=serializer.validated_data["ids"], actor=request.user, request=request)
        return Response(HeroSlideSerializer(slides, many=True).data)


class AdminHeroSlideDetailView(AdminHeroSlideView):
    @extend_schema(tags=TAGS, summary="Hero slide detail", responses=HeroSlideSerializer)
    def get(self, request, slide_id: str):
        return Response(HeroSlideSerializer(get_object_or_404(HeroSlide, pk=slide_id)).data)

    @extend_schema(
        tags=TAGS,
        summary="Update a hero slide (any subset of fields; send image or imageUrl to replace the picture)",
        request={"multipart/form-data": HeroSlideUpsertSerializer, "application/json": HeroSlideUpsertSerializer},
        responses=HeroSlideSerializer,
    )
    def patch(self, request, slide_id: str):
        slide = get_object_or_404(HeroSlide, pk=slide_id)
        serializer = HeroSlideUpsertSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        services.update_hero_slide(slide=slide, data=serializer.validated_data, actor=request.user, request=request)
        return Response(HeroSlideSerializer(slide).data)

    @extend_schema(tags=TAGS, summary="Remove a hero slide", responses={204: None})
    def delete(self, request, slide_id: str):
        slide = get_object_or_404(HeroSlide, pk=slide_id)
        services.delete_hero_slide(slide=slide, actor=request.user, request=request)
        return Response(status=status.HTTP_204_NO_CONTENT)
