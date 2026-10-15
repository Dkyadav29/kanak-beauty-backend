"""Request/response serializers for the courses app. Fields are snake_case; the renderer camelises them."""

from __future__ import annotations

from rest_framework import serializers

from apps.batches.models import LiveSession
from apps.courses.models import Course, CourseModule, LearningMaterial, Lesson, Trainer

# ---------------------------------------------------------------------------
# Trainers
# ---------------------------------------------------------------------------


class TrainerSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()

    class Meta:
        model = Trainer
        fields = ["id", "name", "title", "bio", "avatar_url", "specialities"]
        read_only_fields = fields

    def get_avatar_url(self, obj: Trainer) -> str | None:
        return obj.avatar_url or None


class TrainerUpsertSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=2, max_length=120)
    title = serializers.CharField(min_length=2, max_length=120)
    bio = serializers.CharField(required=False, allow_blank=True, default="")
    avatar_url = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    specialities = serializers.ListField(
        child=serializers.CharField(max_length=80, allow_blank=True), required=False, default=list
    )

    def validate_avatar_url(self, value):
        return (value or "").strip()

    def validate_specialities(self, value):
        return [v.strip() for v in value if v and v.strip()]


# ---------------------------------------------------------------------------
# Curriculum (read)
# ---------------------------------------------------------------------------


class LearningMaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningMaterial
        fields = ["id", "title", "type", "size_label"]
        read_only_fields = fields


class LessonSerializer(serializers.ModelSerializer):
    """Curriculum lesson. Pass ``context={"include_materials": True}`` for admin views; public views get []."""

    materials = LearningMaterialSerializer(many=True, read_only=True)

    class Meta:
        model = Lesson
        fields = [
            "id",
            "module_id",
            "title",
            "type",
            "duration_minutes",
            "order",
            "is_required",
            "summary",
            "materials",
        ]
        read_only_fields = fields

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if not self.context.get("include_materials"):
            data["materials"] = []
        return data


class CourseModuleSerializer(serializers.ModelSerializer):
    lessons = LessonSerializer(many=True, read_only=True)

    class Meta:
        model = CourseModule
        fields = ["id", "course_id", "title", "description", "order", "lessons"]
        read_only_fields = fields


class CompletionCriteriaSerializer(serializers.Serializer):
    require_all_required_lessons = serializers.BooleanField(required=False, default=True)
    min_live_attendance_percent = serializers.IntegerField(
        required=False, allow_null=True, default=None, min_value=0, max_value=100
    )
    require_assessment = serializers.BooleanField(required=False, default=False)


# ---------------------------------------------------------------------------
# Courses (read)
# ---------------------------------------------------------------------------

_COURSE_FIELDS = [
    "id",
    "slug",
    "title",
    "short_description",
    "description",
    "thumbnail_url",
    "trainer",
    "duration_weeks",
    "total_hours",
    "level",
    "format",
    "price",
    "discount_price",
    "currency",
    "status",
    "featured",
    "certificate_enabled",
    "certificate_issuance",
    "completion_criteria",
    "prerequisites",
    "outcomes",
    "schedule_summary",
    "created_at",
    "updated_at",
]


class CourseSummarySerializer(serializers.ModelSerializer):
    """Catalogue-safe summary: course metadata plus module/lesson counts (annotated by the selector)."""

    trainer = TrainerSerializer(read_only=True)
    completion_criteria = CompletionCriteriaSerializer(read_only=True)
    module_count = serializers.IntegerField(read_only=True)
    lesson_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Course
        fields = [*_COURSE_FIELDS, "module_count", "lesson_count"]
        read_only_fields = fields


class CourseSerializer(serializers.ModelSerializer):
    trainer = TrainerSerializer(read_only=True)
    completion_criteria = CompletionCriteriaSerializer(read_only=True)
    modules = CourseModuleSerializer(many=True, read_only=True)

    class Meta:
        model = Course
        fields = [*_COURSE_FIELDS, "modules"]
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Courses (write)
# ---------------------------------------------------------------------------


class CourseUpsertSerializer(serializers.Serializer):
    """CourseUpsertRequest (src/types/course.ts)."""

    title = serializers.CharField(min_length=3, max_length=160)
    short_description = serializers.CharField(min_length=10, max_length=240)
    description = serializers.CharField(min_length=20)
    thumbnail_url = serializers.CharField(required=False, allow_blank=True, allow_null=True, default="")
    trainer_id = serializers.CharField(max_length=40, required=False, allow_blank=True, allow_null=True, default=None)
    duration_weeks = serializers.IntegerField(min_value=1, max_value=520)
    total_hours = serializers.IntegerField(min_value=1, max_value=10000)
    level = serializers.ChoiceField(choices=Course.LEVEL_CHOICES)
    format = serializers.ChoiceField(choices=Course.FORMAT_CHOICES)
    price = serializers.DecimalField(max_digits=12, decimal_places=2, min_value=0)
    discount_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, min_value=0, required=False, allow_null=True, default=None
    )
    status = serializers.ChoiceField(choices=Course.STATUS_CHOICES, required=False)
    featured = serializers.BooleanField(required=False, default=False)
    certificate_enabled = serializers.BooleanField(required=False, default=True)
    certificate_issuance = serializers.ChoiceField(choices=Course.ISSUANCE_CHOICES, required=False, default="automatic")
    completion_criteria = CompletionCriteriaSerializer(required=False)
    prerequisites = serializers.ListField(
        child=serializers.CharField(max_length=240, allow_blank=True), required=False, default=list
    )
    outcomes = serializers.ListField(
        child=serializers.CharField(max_length=240, allow_blank=True), required=False, default=list
    )
    schedule_summary = serializers.CharField(required=False, allow_blank=True, max_length=240, default="")

    def validate_thumbnail_url(self, value):
        return (value or "").strip()

    def validate_trainer_id(self, value):
        value = (value or "").strip()
        if not value:
            return None  # trainer is optional; assign later
        if not Trainer.objects.filter(pk=value).exists():
            raise serializers.ValidationError("Select a trainer")
        return value

    def validate_prerequisites(self, value):
        return [v.strip() for v in value if v and v.strip()]

    def validate_outcomes(self, value):
        return [v.strip() for v in value if v and v.strip()]

    def validate(self, attrs):
        discount = attrs.get("discount_price")
        if discount is not None and discount >= attrs["price"]:
            raise serializers.ValidationError({"discount_price": ["Must be lower than price"]})
        return attrs


class CurriculumLessonInputSerializer(serializers.Serializer):
    id = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=40)
    title = serializers.CharField(min_length=2, max_length=160)
    type = serializers.ChoiceField(choices=Lesson.TYPE_CHOICES, required=False, default="video")
    duration_minutes = serializers.IntegerField(min_value=1, max_value=10000, required=False, default=10)
    is_required = serializers.BooleanField(required=False, default=True)
    summary = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=400, default="")


class CurriculumModuleInputSerializer(serializers.Serializer):
    id = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=40)
    title = serializers.CharField(min_length=2, max_length=160)
    description = serializers.CharField(required=False, allow_blank=True, allow_null=True, max_length=400, default="")
    lessons = CurriculumLessonInputSerializer(many=True, required=False, default=list)


class CurriculumUpdateSerializer(serializers.Serializer):
    modules = CurriculumModuleInputSerializer(many=True)


class ThumbnailUploadSerializer(serializers.Serializer):
    file = serializers.FileField()


class ThumbnailResponseSerializer(serializers.Serializer):
    url = serializers.CharField()


# ---------------------------------------------------------------------------
# Misc read models
# ---------------------------------------------------------------------------


class EnrolledStudentSerializer(serializers.Serializer):
    student_id = serializers.CharField()
    name = serializers.CharField()
    email = serializers.EmailField()
    batch_id = serializers.CharField(allow_null=True)


class AcademyStatsSerializer(serializers.Serializer):
    courses_offered = serializers.IntegerField()
    enrolled_students = serializers.IntegerField()
    expert_trainers = serializers.IntegerField()
    certificates_issued = serializers.IntegerField()
    courses_completed = serializers.IntegerField()
    is_demo_data = serializers.BooleanField()


class PublicLiveSessionSerializer(serializers.ModelSerializer):
    """LiveSession (src/types/live.ts) without join information or provider references."""

    course_title = serializers.CharField(source="course.title", read_only=True)
    trainer_name = serializers.CharField(source="trainer.name", read_only=True)
    recording_id = serializers.SerializerMethodField()

    class Meta:
        model = LiveSession
        fields = [
            "id",
            "course_id",
            "course_title",
            "batch_id",
            "title",
            "trainer_id",
            "trainer_name",
            "starts_at",
            "duration_minutes",
            "status",
            "join_window_minutes_before",
            "recording_id",
            "description",
        ]
        read_only_fields = fields

    def get_recording_id(self, obj: LiveSession) -> str | None:
        annotated = getattr(obj, "recording_pk", None)
        if annotated is not None:
            return annotated
        try:
            return obj.recording.id
        except LiveSession.recording.RelatedObjectDoesNotExist:
            return None
