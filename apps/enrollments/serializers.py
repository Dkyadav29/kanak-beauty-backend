"""Serializers for student learning views and admin enrollment management (snake_case -> camelCase on the wire)."""

from __future__ import annotations

from rest_framework import serializers

from apps.certificates.models import Certificate
from apps.courses.models import Course, CourseModule, LearningMaterial, Lesson, Trainer
from apps.enrollments.models import CourseEnrollment, LessonProgress


class _NullableCharField(serializers.CharField):
    """Blank strings are exposed as null (frontend types use `string | null`)."""

    def to_representation(self, value):
        return value if value else None


# --- Course (student copy of the full curriculum) ---------------------------------------------


class StudentTrainerSerializer(serializers.ModelSerializer):
    avatar_url = _NullableCharField()

    class Meta:
        model = Trainer
        fields = ["id", "name", "title", "bio", "avatar_url", "specialities"]


class StudentLearningMaterialSerializer(serializers.ModelSerializer):
    class Meta:
        model = LearningMaterial
        fields = ["id", "title", "type", "size_label"]


class StudentLessonSerializer(serializers.ModelSerializer):
    materials = StudentLearningMaterialSerializer(many=True, read_only=True)

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


class StudentCourseModuleSerializer(serializers.ModelSerializer):
    lessons = StudentLessonSerializer(many=True, read_only=True)

    class Meta:
        model = CourseModule
        fields = ["id", "course_id", "title", "description", "order", "lessons"]


class StudentCompletionCriteriaSerializer(serializers.Serializer):
    require_all_required_lessons = serializers.BooleanField()
    min_live_attendance_percent = serializers.IntegerField(allow_null=True)
    require_assessment = serializers.BooleanField()


class StudentCourseSerializer(serializers.ModelSerializer):
    trainer = StudentTrainerSerializer(read_only=True)
    completion_criteria = StudentCompletionCriteriaSerializer(read_only=True)
    modules = StudentCourseModuleSerializer(many=True, read_only=True)

    class Meta:
        model = Course
        fields = [
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
            "modules",
            "created_at",
            "updated_at",
        ]


# --- Progress ----------------------------------------------------------------------------------


class LessonProgressSerializer(serializers.ModelSerializer):
    class Meta:
        model = LessonProgress
        fields = [
            "id",
            "student_id",
            "course_id",
            "lesson_id",
            "completed",
            "completed_at",
            "last_position_seconds",
            "updated_at",
        ]


class CourseProgressSummarySerializer(serializers.Serializer):
    course_id = serializers.CharField()
    completed_lessons = serializers.IntegerField()
    total_lessons = serializers.IntegerField()
    required_lessons = serializers.IntegerField()
    completed_required_lessons = serializers.IntegerField()
    progress_percent = serializers.IntegerField()
    last_lesson_id = serializers.CharField(allow_null=True)
    lessons = LessonProgressSerializer(many=True)
    live_attendance_percent = serializers.IntegerField(allow_null=True)
    assessment_passed = serializers.BooleanField(allow_null=True)
    completion_eligible = serializers.BooleanField()
    is_completed = serializers.BooleanField()
    completed_at = serializers.DateTimeField(allow_null=True)


class UpdateLessonProgressSerializer(serializers.Serializer):
    completed = serializers.BooleanField(required=False, allow_null=True)
    last_position_seconds = serializers.IntegerField(required=False, allow_null=True, min_value=0)


# --- Enrollments -------------------------------------------------------------------------------


class CourseEnrollmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = CourseEnrollment
        fields = [
            "id",
            "student_id",
            "course_id",
            "batch_id",
            "source",
            "access_status",
            "payment_id",
            "enrolled_at",
            "completed_at",
        ]


class EnrolledCourseViewSerializer(serializers.Serializer):
    enrollment = CourseEnrollmentSerializer()
    course_id = serializers.CharField()
    title = serializers.CharField()
    thumbnail_url = serializers.CharField()
    trainer_name = serializers.CharField()
    format = serializers.ChoiceField(choices=Course.FORMAT_CHOICES)
    progress_percent = serializers.IntegerField()
    completed_lessons = serializers.IntegerField()
    total_lessons = serializers.IntegerField()
    next_session_at = serializers.DateTimeField(allow_null=True)
    is_completed = serializers.BooleanField()
    certificate_id = serializers.CharField(allow_null=True)


# --- Dashboard ---------------------------------------------------------------------------------


class DashboardLiveSessionSerializer(serializers.Serializer):
    id = serializers.CharField()
    course_id = serializers.CharField()
    course_title = serializers.CharField()
    batch_id = serializers.CharField(allow_null=True)
    title = serializers.CharField()
    trainer_id = serializers.CharField()
    trainer_name = serializers.CharField()
    starts_at = serializers.DateTimeField()
    duration_minutes = serializers.IntegerField()
    status = serializers.CharField()
    join_window_minutes_before = serializers.IntegerField()
    recording_id = serializers.CharField(allow_null=True)
    description = serializers.CharField()
    is_enrolled = serializers.BooleanField()
    can_join_now = serializers.BooleanField()
    join_opens_at = serializers.DateTimeField()
    join_closes_at = serializers.DateTimeField()


class DashboardCertificateSerializer(serializers.ModelSerializer):
    revoked_reason = _NullableCharField()
    verification_url = serializers.SerializerMethodField()

    class Meta:
        model = Certificate
        fields = [
            "id",
            "certificate_number",
            "student_id",
            "student_name",
            "course_id",
            "course_title",
            "batch_id",
            "completion_date",
            "issued_at",
            "issued_by",
            "status",
            "revoked_at",
            "revoked_reason",
            "verification_url",
        ]

    def get_verification_url(self, obj) -> str:
        return obj.verification_path


class DashboardStatsSerializer(serializers.Serializer):
    total_enrolled = serializers.IntegerField()
    active = serializers.IntegerField()
    upcoming_live_classes = serializers.IntegerField()
    completed = serializers.IntegerField()
    certificates = serializers.IntegerField()


class StudentDashboardSerializer(serializers.Serializer):
    stats = DashboardStatsSerializer()
    continue_learning = EnrolledCourseViewSerializer(allow_null=True)
    upcoming_sessions = DashboardLiveSessionSerializer(many=True)
    recent_courses = EnrolledCourseViewSerializer(many=True)
    recent_certificates = DashboardCertificateSerializer(many=True)


# --- Admin -------------------------------------------------------------------------------------


class EnrollmentAdminRowSerializer(serializers.Serializer):
    enrollment_id = serializers.CharField(source="id")
    student_id = serializers.CharField()
    student_name = serializers.CharField(source="student.name")
    student_email = serializers.EmailField(source="student.email")
    course_id = serializers.CharField()
    course_title = serializers.CharField(source="course.title")
    batch_id = serializers.CharField(allow_null=True)
    batch_name = serializers.SerializerMethodField()
    order_id = serializers.SerializerMethodField()
    amount = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    payment_status = serializers.SerializerMethodField()
    refund_status = serializers.SerializerMethodField()
    access_status = serializers.ChoiceField(choices=CourseEnrollment.ACCESS_CHOICES)
    source = serializers.ChoiceField(choices=CourseEnrollment.SOURCE_CHOICES)
    enrolled_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)

    def get_batch_name(self, obj) -> str | None:
        return obj.batch.name if obj.batch_id else None

    def get_order_id(self, obj) -> str | None:
        return obj.payment.order_id if obj.payment_id else None

    def get_amount(self, obj) -> float | None:
        return obj.payment.amount if obj.payment_id else None

    def get_currency(self, obj) -> str:
        return obj.payment.currency if obj.payment_id else "INR"

    def get_payment_status(self, obj) -> str:
        return obj.payment.status if obj.payment_id else "none"

    def get_refund_status(self, obj) -> str:
        return obj.payment.refund_status if obj.payment_id else "none"


class GrantEnrollmentSerializer(serializers.Serializer):
    student_id = serializers.CharField()
    course_id = serializers.CharField()
    reason = serializers.CharField(min_length=5, max_length=240)


class EnrollmentAccessSerializer(serializers.Serializer):
    access_status = serializers.ChoiceField(choices=[("active", "Active"), ("suspended", "Suspended")])
    reason = serializers.CharField(min_length=5, max_length=240)
