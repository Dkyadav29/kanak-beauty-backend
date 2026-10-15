"""Read-only report serializers (admin overview, analytics, student directory/detail)."""

from __future__ import annotations

from rest_framework import serializers

from apps.accounts.models import User
from apps.certificates.models import Certificate
from apps.payments.models import PaymentTransaction


class DateRangeSerializer(serializers.Serializer):
    from_ = serializers.DateTimeField()  # renamed to "from" (a Python keyword) in get_fields
    to = serializers.DateTimeField()

    def get_fields(self):
        fields = super().get_fields()
        fields["from"] = fields.pop("from_")
        return fields


class AdminOverviewStatsSerializer(serializers.Serializer):
    total_students = serializers.IntegerField()
    active_students = serializers.IntegerField()
    total_courses = serializers.IntegerField()
    published_courses = serializers.IntegerField()
    active_batches = serializers.IntegerField()
    upcoming_live_classes = serializers.IntegerField()
    paid_enrollments = serializers.IntegerField()
    total_payments_received = serializers.DecimalField(max_digits=14, decimal_places=2)
    pending_payments = serializers.IntegerField()
    pending_payment_amount = serializers.DecimalField(max_digits=14, decimal_places=2)
    completed_courses = serializers.IntegerField()
    certificates_issued = serializers.IntegerField()
    currency = serializers.CharField()
    range = DateRangeSerializer()


class RevenuePointSerializer(serializers.Serializer):
    month = serializers.CharField()
    revenue = serializers.DecimalField(max_digits=14, decimal_places=2)
    enrollments = serializers.IntegerField()


class CourseEnrollmentPointSerializer(serializers.Serializer):
    course_id = serializers.CharField()
    course_title = serializers.CharField()
    enrollments = serializers.IntegerField()
    completions = serializers.IntegerField()


class AdminAnalyticsSerializer(serializers.Serializer):
    revenue_by_month = RevenuePointSerializer(many=True)
    enrollments_by_course = CourseEnrollmentPointSerializer(many=True)


class StudentDirectoryRowSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()
    enrolled_courses = serializers.IntegerField(read_only=True)
    completed_courses = serializers.IntegerField(read_only=True)
    certificates = serializers.IntegerField(source="certificate_count", read_only=True)
    last_payment_status = serializers.CharField(read_only=True)

    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "email",
            "mobile",
            "avatar_url",
            "created_at",
            "enrolled_courses",
            "completed_courses",
            "certificates",
            "last_payment_status",
            "is_active",
        ]
        read_only_fields = fields

    def get_avatar_url(self, obj) -> str | None:
        return obj.avatar_url or None


class StudentProfileAdminSerializer(serializers.ModelSerializer):
    avatar_url = serializers.SerializerMethodField()
    address = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = [
            "id",
            "name",
            "email",
            "mobile",
            "avatar_url",
            "created_at",
            "email_verified",
            "date_of_birth",
            "address",
        ]
        read_only_fields = fields

    def get_avatar_url(self, obj) -> str | None:
        return obj.avatar_url or None

    def get_address(self, obj) -> str | None:
        return obj.address or None


class StudentEnrollmentAdminSerializer(serializers.Serializer):
    enrollment_id = serializers.CharField()
    course_id = serializers.CharField()
    course_title = serializers.CharField()
    batch_name = serializers.CharField(allow_null=True)
    access_status = serializers.CharField()
    progress_percent = serializers.IntegerField()
    enrolled_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)
    attendance_percent = serializers.IntegerField(allow_null=True)


class StudentPaymentAdminSerializer(serializers.ModelSerializer):
    course_title = serializers.CharField(source="course.title", read_only=True)

    class Meta:
        model = PaymentTransaction
        fields = ["id", "order_id", "course_title", "amount", "currency", "status", "created_at"]
        read_only_fields = fields


class StudentCertificateAdminSerializer(serializers.ModelSerializer):
    class Meta:
        model = Certificate
        fields = ["id", "certificate_number", "course_title", "issued_at", "status"]
        read_only_fields = fields


class StudentDetailAdminSerializer(serializers.Serializer):
    profile = StudentProfileAdminSerializer()
    enrollments = StudentEnrollmentAdminSerializer(many=True)
    payments = StudentPaymentAdminSerializer(many=True)
    certificates = StudentCertificateAdminSerializer(many=True)
