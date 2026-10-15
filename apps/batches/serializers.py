"""Request/response serializers for batches, live sessions, attendance and recordings (camelCase on the wire)."""

from __future__ import annotations

from datetime import datetime

from django.utils import timezone
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from apps.accounts.models import User
from apps.batches.models import AttendanceRecord, Batch, BatchEnrollment, BatchHistoryEvent, LiveSession, Recording
from apps.batches.selectors import can_join_now, join_window
from apps.courses.models import Course, Lesson, Trainer

# ---------------------------------------------------------------------------
# Live sessions
# ---------------------------------------------------------------------------


class LiveSessionSerializer(serializers.ModelSerializer):
    """Public/admin LiveSession shape (never includes provider_meeting_ref)."""

    course_id = serializers.CharField(read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    batch_id = serializers.CharField(read_only=True, allow_null=True)
    trainer_id = serializers.CharField(read_only=True)
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

    def get_recording_id(self, obj) -> str | None:
        recording = getattr(obj, "recording", None)
        return recording.id if recording is not None else None


class StudentLiveSessionSerializer(LiveSessionSerializer):
    """LiveSession plus entitlement flags computed server-side."""

    is_enrolled = serializers.SerializerMethodField()
    can_join_now = serializers.SerializerMethodField()
    join_opens_at = serializers.SerializerMethodField()
    join_closes_at = serializers.SerializerMethodField()

    class Meta(LiveSessionSerializer.Meta):
        fields = LiveSessionSerializer.Meta.fields + ["is_enrolled", "can_join_now", "join_opens_at", "join_closes_at"]
        read_only_fields = fields

    def _now(self):
        return self.context.get("now") or timezone.now()

    def get_is_enrolled(self, obj) -> bool:
        # Querysets from sessions_visible_to() only contain visible sessions.
        return bool(self.context.get("visible", True))

    def get_can_join_now(self, obj) -> bool:
        return can_join_now(obj, visible=self.get_is_enrolled(obj), now=self._now())

    def get_join_opens_at(self, obj) -> datetime:
        return serializers.DateTimeField().to_representation(join_window(obj)[0])

    def get_join_closes_at(self, obj) -> datetime:
        return serializers.DateTimeField().to_representation(join_window(obj)[1])


class JoinSessionResponseSerializer(serializers.Serializer):
    join_url = serializers.URLField()
    provider = serializers.CharField()
    expires_at = serializers.DateTimeField()


class LiveSessionUpsertSerializer(serializers.Serializer):
    course_id = serializers.PrimaryKeyRelatedField(queryset=Course.objects.all(), source="course")
    batch_id = serializers.PrimaryKeyRelatedField(
        queryset=Batch.objects.all(), source="batch", allow_null=True, required=False
    )
    title = serializers.CharField(min_length=3, max_length=160)
    trainer_id = serializers.PrimaryKeyRelatedField(queryset=Trainer.objects.all(), source="trainer")
    starts_at = serializers.DateTimeField()
    duration_minutes = serializers.IntegerField(min_value=15, max_value=600, required=False, default=60)
    description = serializers.CharField(max_length=400, allow_blank=True, required=False, default="")
    join_window_minutes_before = serializers.IntegerField(min_value=0, max_value=1440, required=False, default=15)
    lesson_id = serializers.PrimaryKeyRelatedField(
        queryset=Lesson.objects.all(), source="lesson", allow_null=True, required=False
    )

    def validate(self, attrs):
        course = attrs["course"]
        batch = attrs.get("batch")
        if batch is not None and batch.course_id != course.id:
            raise serializers.ValidationError({"batch_id": ["Batch does not belong to this course."]})
        lesson = attrs.get("lesson")
        if lesson is not None and lesson.module.course_id != course.id:
            raise serializers.ValidationError({"lesson_id": ["Lesson does not belong to this course."]})
        return attrs


class AttendanceRecordInputSerializer(serializers.Serializer):
    student_id = serializers.CharField(max_length=40)
    status = serializers.ChoiceField(choices=[c[0] for c in AttendanceRecord.STATUS_CHOICES])


class AttendanceRequestSerializer(serializers.Serializer):
    records = AttendanceRecordInputSerializer(many=True, allow_empty=False)

    def validate_records(self, value):
        ids = [r["student_id"] for r in value]
        if len(ids) != len(set(ids)):
            raise serializers.ValidationError("Each student may appear only once.")
        return value


class AttendanceResponseSerializer(serializers.Serializer):
    session_id = serializers.CharField()
    recorded = serializers.IntegerField()


# ---------------------------------------------------------------------------
# Recordings
# ---------------------------------------------------------------------------


class RecordingSerializer(serializers.ModelSerializer):
    session_id = serializers.CharField(read_only=True)
    course_id = serializers.CharField(read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    trainer_name = serializers.CharField(source="session.trainer.name", read_only=True)
    lesson_id = serializers.CharField(read_only=True, allow_null=True)

    class Meta:
        model = Recording
        fields = [
            "id",
            "session_id",
            "course_id",
            "course_title",
            "title",
            "trainer_name",
            "recorded_at",
            "duration_minutes",
            "status",
            "lesson_id",
        ]
        read_only_fields = fields


class RecordingPlaybackResponseSerializer(serializers.Serializer):
    stream_url = serializers.CharField()
    expires_at = serializers.DateTimeField()
    resume_position_seconds = serializers.IntegerField()


class RecordingUpsertSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[c[0] for c in Recording.STATUS_CHOICES])
    storage_key = serializers.CharField(max_length=255, required=False, allow_blank=True)
    duration_minutes = serializers.IntegerField(min_value=1, max_value=600, required=False)
    title = serializers.CharField(max_length=160, required=False)
    lesson_id = serializers.PrimaryKeyRelatedField(
        queryset=Lesson.objects.all(), source="lesson", allow_null=True, required=False
    )

    def validate(self, attrs):
        if attrs["status"] == "available" and not (
            attrs.get("storage_key") or (self.instance and self.instance.storage_key)
        ):
            raise serializers.ValidationError(
                {"storage_key": ["A storage key is required to mark a recording available."]}
            )
        return attrs


# ---------------------------------------------------------------------------
# Batches
# ---------------------------------------------------------------------------


class BatchSerializer(serializers.ModelSerializer):
    course_id = serializers.CharField(read_only=True)
    trainer_id = serializers.CharField(read_only=True)

    class Meta:
        model = Batch
        fields = [
            "id",
            "code",
            "name",
            "course_id",
            "trainer_id",
            "start_date",
            "end_date",
            "capacity",
            "status",
            "schedule_note",
            "created_at",
            "updated_at",
        ]
        read_only_fields = fields


class BatchSummarySerializer(BatchSerializer):
    """Batch + course/trainer names + membership counters (annotated by batch_summary_queryset)."""

    course_title = serializers.CharField(source="course.title", read_only=True)
    trainer_name = serializers.CharField(source="trainer.name", read_only=True)
    total_students = serializers.IntegerField(read_only=True)
    completed_students = serializers.IntegerField(read_only=True)
    in_progress_students = serializers.IntegerField(read_only=True)
    withdrawn_students = serializers.IntegerField(read_only=True)
    session_count = serializers.IntegerField(read_only=True)

    class Meta(BatchSerializer.Meta):
        fields = BatchSerializer.Meta.fields + [
            "course_title",
            "trainer_name",
            "total_students",
            "completed_students",
            "in_progress_students",
            "withdrawn_students",
            "session_count",
        ]
        read_only_fields = fields


class BatchEnrollmentSerializer(serializers.ModelSerializer):
    batch_id = serializers.CharField(read_only=True)
    student_id = serializers.CharField(read_only=True)

    class Meta:
        model = BatchEnrollment
        fields = ["id", "batch_id", "student_id", "status", "enrolled_at", "completed_at", "withdrawn_at"]
        read_only_fields = fields


class BatchHistoryEventSerializer(serializers.ModelSerializer):
    batch_id = serializers.CharField(read_only=True)
    actor_id = serializers.CharField(read_only=True, allow_null=True)

    class Meta:
        model = BatchHistoryEvent
        fields = ["id", "batch_id", "type", "description", "actor_id", "actor_name", "at"]
        read_only_fields = fields


class BatchStudentRowSerializer(serializers.Serializer):
    enrollment = BatchEnrollmentSerializer()
    student_id = serializers.CharField()
    student_name = serializers.CharField()
    student_email = serializers.EmailField()
    progress_percent = serializers.IntegerField()
    attendance_percent = serializers.IntegerField(allow_null=True)
    certificate_number = serializers.CharField(allow_null=True)


class BatchDetailSerializer(BatchSummarySerializer):
    """BatchSummary + students, full history (newest first) and session ids. Context: students, history, session_ids."""

    students = serializers.SerializerMethodField()
    history = serializers.SerializerMethodField()
    session_ids = serializers.SerializerMethodField()

    class Meta(BatchSummarySerializer.Meta):
        fields = BatchSummarySerializer.Meta.fields + ["students", "history", "session_ids"]
        read_only_fields = fields

    @extend_schema_field(BatchStudentRowSerializer(many=True))
    def get_students(self, obj):
        return BatchStudentRowSerializer(self.context.get("students", []), many=True).data

    @extend_schema_field(BatchHistoryEventSerializer(many=True))
    def get_history(self, obj):
        return BatchHistoryEventSerializer(self.context.get("history", []), many=True).data

    def get_session_ids(self, obj) -> list[str]:
        return list(self.context.get("session_ids", []))


class BatchUpsertSerializer(serializers.Serializer):
    name = serializers.CharField(min_length=3, max_length=160)
    course_id = serializers.PrimaryKeyRelatedField(queryset=Course.objects.all(), source="course")
    trainer_id = serializers.PrimaryKeyRelatedField(queryset=Trainer.objects.all(), source="trainer")
    start_date = serializers.DateField()
    end_date = serializers.DateField()
    capacity = serializers.IntegerField(min_value=1, max_value=1000)
    schedule_note = serializers.CharField(max_length=240, allow_blank=True, required=False, default="")

    def validate(self, attrs):
        if attrs["end_date"] < attrs["start_date"]:
            raise serializers.ValidationError({"end_date": ["End date must be on or after the start date."]})
        return attrs


class BatchStatusChangeSerializer(serializers.Serializer):
    status = serializers.ChoiceField(choices=[c[0] for c in Batch.STATUS_CHOICES])
    reason = serializers.CharField(max_length=240, required=False, allow_blank=True, default="")


class AddBatchStudentSerializer(serializers.Serializer):
    student_id = serializers.CharField(max_length=40)


class WithdrawBatchStudentSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=240, required=False, allow_blank=True, default="")


def student_or_none(student_id: str) -> User | None:
    return User.objects.filter(pk=student_id, role="student").first()
