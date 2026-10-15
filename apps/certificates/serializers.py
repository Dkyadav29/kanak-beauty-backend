"""Certificate serializers (snake_case here -> camelCase on the wire)."""

from __future__ import annotations

from rest_framework import serializers

from .models import Certificate


class CertificateSerializer(serializers.ModelSerializer):
    """Matches ``Certificate`` in src/types/certificate.ts."""

    student_id = serializers.CharField(read_only=True)
    course_id = serializers.CharField(read_only=True)
    batch_id = serializers.CharField(read_only=True, allow_null=True)
    revoked_reason = serializers.SerializerMethodField()
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
        read_only_fields = fields

    def get_revoked_reason(self, obj) -> str | None:
        return obj.revoked_reason or None

    def get_verification_url(self, obj) -> str:
        return obj.verification_path


class VerifiedCertificateSerializer(serializers.Serializer):
    certificate_number = serializers.CharField()
    student_name = serializers.CharField()
    course_title = serializers.CharField()
    completion_date = serializers.DateTimeField()
    issued_at = serializers.DateTimeField()
    issued_by = serializers.CharField()
    status = serializers.ChoiceField(choices=Certificate.STATUS_CHOICES)


class CertificateVerificationResultSerializer(serializers.Serializer):
    valid = serializers.BooleanField()
    status = serializers.ChoiceField(choices=[("issued", "Issued"), ("revoked", "Revoked"), ("not_found", "Not found")])
    certificate = VerifiedCertificateSerializer(allow_null=True)
    checked_at = serializers.DateTimeField()


class EligibilityEvidenceSerializer(serializers.Serializer):
    required_lessons_completed = serializers.CharField()
    live_attendance_percent = serializers.IntegerField(allow_null=True)
    assessment_passed = serializers.BooleanField(allow_null=True)


class EligibleStudentSerializer(serializers.Serializer):
    student_id = serializers.CharField()
    student_name = serializers.CharField()
    student_email = serializers.EmailField()
    course_id = serializers.CharField()
    course_title = serializers.CharField()
    batch_id = serializers.CharField(allow_null=True)
    completed_at = serializers.DateTimeField()
    evidence = EligibilityEvidenceSerializer()


class IssueCertificateRequestSerializer(serializers.Serializer):
    student_id = serializers.CharField(max_length=40)
    course_id = serializers.CharField(max_length=40)


class CertificateReasonSerializer(serializers.Serializer):
    reason = serializers.CharField(min_length=5, max_length=240, trim_whitespace=True)
