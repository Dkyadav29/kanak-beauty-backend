"""Site content managed by admins: the public home page hero slider."""

from django.db import models

from common.models import PrefixedIDModel


class HeroSlide(PrefixedIDModel):
    """
    One slide of the home page hero carousel. The image is either uploaded
    (``image_key`` holds its storage name and ``image_url`` the public serve path)
    or an external https URL (``image_key`` empty).
    """

    ID_PREFIX = "hero"

    FOCUS_CHOICES = [
        ("center", "Center"),
        ("top", "Top"),
        ("bottom", "Bottom"),
        ("left", "Left"),
        ("right", "Right"),
        ("left-top", "Left top"),
        ("right-top", "Right top"),
        ("left-bottom", "Left bottom"),
        ("right-bottom", "Right bottom"),
    ]

    image_url = models.TextField()
    image_key = models.CharField(max_length=255, blank=True, default="", db_index=True)
    alt = models.CharField(max_length=200)
    eyebrow = models.CharField(max_length=60, blank=True, default="")
    caption = models.CharField(max_length=160, blank=True, default="")
    focus = models.CharField(max_length=16, choices=FOCUS_CHOICES, default="center")
    sort_order = models.PositiveIntegerField(default=0, db_index=True)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        db_table = "content_hero_slide"
        ordering = ["sort_order", "created_at"]

    def __str__(self) -> str:
        return self.eyebrow or self.alt
