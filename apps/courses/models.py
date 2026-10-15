"""Courses: trainers, courses, curriculum (modules, lessons, materials)."""

from django.db import models
from django.utils.text import slugify

from common.models import PrefixedIDModel


class Trainer(PrefixedIDModel):
    ID_PREFIX = "tr"

    name = models.CharField(max_length=120)
    title = models.CharField(max_length=120)
    bio = models.TextField(blank=True, default="")
    avatar_url = models.TextField(blank=True, default="")
    specialities = models.JSONField(default=list, blank=True)  # list[str]
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = "courses_trainer"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Course(PrefixedIDModel):
    ID_PREFIX = "crs"

    LEVEL_CHOICES = [("beginner", "Beginner"), ("intermediate", "Intermediate"), ("advanced", "Advanced")]
    FORMAT_CHOICES = [("live", "Live"), ("recorded", "Recorded"), ("hybrid", "Hybrid")]
    STATUS_CHOICES = [("draft", "Draft"), ("published", "Published"), ("unpublished", "Unpublished")]
    ISSUANCE_CHOICES = [("automatic", "Automatic"), ("manual_approval", "Manual approval")]

    slug = models.SlugField(max_length=160, unique=True)
    title = models.CharField(max_length=160)
    short_description = models.CharField(max_length=240)
    description = models.TextField()
    thumbnail_url = models.TextField(blank=True, default="")
    # Optional: a course can be created before a trainer is assigned.
    trainer = models.ForeignKey(Trainer, on_delete=models.SET_NULL, null=True, blank=True, related_name="courses")
    duration_weeks = models.PositiveSmallIntegerField()
    total_hours = models.PositiveSmallIntegerField()
    level = models.CharField(max_length=16, choices=LEVEL_CHOICES, db_index=True)
    format = models.CharField(max_length=16, choices=FORMAT_CHOICES, db_index=True)
    price = models.DecimalField(max_digits=12, decimal_places=2)
    discount_price = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    currency = models.CharField(max_length=3, default="INR")
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="draft", db_index=True)
    featured = models.BooleanField(default=False)
    certificate_enabled = models.BooleanField(default=True)
    certificate_issuance = models.CharField(max_length=20, choices=ISSUANCE_CHOICES, default="automatic")
    # Completion criteria
    require_all_required_lessons = models.BooleanField(default=True)
    min_live_attendance_percent = models.PositiveSmallIntegerField(null=True, blank=True)
    require_assessment = models.BooleanField(default=False)
    prerequisites = models.JSONField(default=list, blank=True)  # list[str]
    outcomes = models.JSONField(default=list, blank=True)  # list[str]
    schedule_summary = models.CharField(max_length=240, blank=True, default="")

    class Meta:
        db_table = "courses_course"
        ordering = ["-updated_at"]
        constraints = [
            models.CheckConstraint(
                condition=models.Q(discount_price__isnull=True) | models.Q(discount_price__lt=models.F("price")),
                name="course_discount_below_price",
            ),
            models.CheckConstraint(
                condition=models.Q(min_live_attendance_percent__isnull=True)
                | models.Q(min_live_attendance_percent__lte=100),
                name="course_attendance_pct_max_100",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.title)[:140] or "course"
            slug, n = base, 2
            while Course.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                slug = f"{base}-{n}"
                n += 1
            self.slug = slug
        super().save(*args, **kwargs)

    @property
    def effective_price(self):
        return self.discount_price if self.discount_price is not None else self.price

    @property
    def completion_criteria(self) -> dict:
        return {
            "require_all_required_lessons": self.require_all_required_lessons,
            "min_live_attendance_percent": self.min_live_attendance_percent,
            "require_assessment": self.require_assessment,
        }


class CourseModule(PrefixedIDModel):
    ID_PREFIX = "mod"

    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="modules")
    title = models.CharField(max_length=160)
    description = models.CharField(max_length=400, blank=True, default="")
    order = models.PositiveSmallIntegerField()

    class Meta:
        db_table = "courses_module"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "order"], name="module_order_unique_per_course", deferrable=models.Deferrable.DEFERRED
            )
        ]

    def __str__(self) -> str:
        return f"{self.course_id} / {self.order}. {self.title}"


class Lesson(PrefixedIDModel):
    ID_PREFIX = "les"

    TYPE_CHOICES = [("video", "Video"), ("reading", "Reading"), ("live", "Live"), ("assessment", "Assessment")]

    module = models.ForeignKey(CourseModule, on_delete=models.CASCADE, related_name="lessons")
    title = models.CharField(max_length=160)
    type = models.CharField(max_length=16, choices=TYPE_CHOICES, default="video")
    duration_minutes = models.PositiveSmallIntegerField(default=10)
    order = models.PositiveSmallIntegerField()
    is_required = models.BooleanField(default=True)
    summary = models.CharField(max_length=400, blank=True, default="")
    # Content references are never exposed publicly; the student endpoint returns signed URLs.
    video_storage_key = models.CharField(max_length=255, blank=True, default="")
    content = models.TextField(blank=True, default="")

    class Meta:
        db_table = "courses_lesson"
        ordering = ["order"]
        constraints = [
            models.UniqueConstraint(
                fields=["module", "order"], name="lesson_order_unique_per_module", deferrable=models.Deferrable.DEFERRED
            )
        ]

    def __str__(self) -> str:
        return self.title


class LearningMaterial(PrefixedIDModel):
    ID_PREFIX = "mat"

    TYPE_CHOICES = [("pdf", "PDF"), ("image", "Image"), ("link", "Link")]

    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="materials")
    title = models.CharField(max_length=160)
    type = models.CharField(max_length=8, choices=TYPE_CHOICES, default="pdf")
    storage_key = models.CharField(max_length=255, blank=True, default="")  # or external URL for type=link
    size_label = models.CharField(max_length=20, blank=True, default="")

    class Meta:
        db_table = "courses_learning_material"
        ordering = ["created_at"]
