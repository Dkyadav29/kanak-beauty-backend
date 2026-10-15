"""seed_demo management command: builds the frontend demo academy and is idempotent."""

from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import CommandError, call_command
from django.utils import timezone

from apps.accounts.models import User
from apps.batches.models import AttendanceRecord, Batch, BatchEnrollment, BatchHistoryEvent, LiveSession, Recording
from apps.certificates.models import Certificate
from apps.core.models import AuditLog
from apps.courses.models import Course, Lesson, Trainer
from apps.enrollments.models import CourseEnrollment, LessonProgress
from apps.payments.models import PaymentTransaction
from apps.referrals.models import Referral, ReferralProgramConfig, ReferralReward

pytestmark = pytest.mark.django_db

MODELS = [
    User,
    Trainer,
    Course,
    Lesson,
    Batch,
    BatchEnrollment,
    BatchHistoryEvent,
    LiveSession,
    Recording,
    AttendanceRecord,
    PaymentTransaction,
    CourseEnrollment,
    LessonProgress,
    Certificate,
    Referral,
    ReferralReward,
    AuditLog,
]


def _counts() -> dict[str, int]:
    return {m.__name__: m.objects.count() for m in MODELS}


def _run(*args) -> str:
    out = StringIO()
    call_command("seed_demo", *args, force=True, stdout=out)
    return out.getvalue()


def test_refuses_without_debug(settings):
    settings.DEBUG = False
    with pytest.raises(CommandError):
        call_command("seed_demo")
    assert User.objects.count() == 0


def test_seed_builds_demo_academy():
    output = _run()
    assert (
        "Demo logins" in output
        and "admin@kanakacademy.demo / Admin@123" in output
        and "priya@student.demo / Student@123" in output
    )

    admin = User.objects.get(email="admin@kanakacademy.demo")
    assert admin.role == "admin" and admin.check_password("Admin@123") and admin.email_verified
    priya = User.objects.get(email="priya@student.demo")
    assert priya.role == "student" and priya.check_password("Student@123")
    assert User.objects.get(email="ananya@student.demo").check_password("Student@123")
    assert User.objects.filter(role="student").count() == 12
    assert not User.objects.get(email="zoya.ali@example.demo").email_verified

    assert Trainer.objects.count() == 4
    assert Course.objects.count() == 7
    assert Course.objects.filter(status="draft").count() == 1
    assert Course.objects.get(pk="crs_makeup").modules.count() == 3
    assert Lesson.objects.filter(module__course_id="crs_makeup").count() == 13
    assert Lesson.objects.get(pk="crs_makeup_m3_l5").is_required is False
    assert set(Batch.objects.values_list("status", flat=True)) == {
        "archived",
        "active",
        "scheduled",
        "completed",
        "cancelled",
    }

    # Priya: makeup in progress (batch 3), skincare completed + certificate, bridal upcoming, failed salon order
    assert set(CourseEnrollment.objects.filter(student=priya).values_list("course_id", flat=True)) == {
        "crs_makeup",
        "crs_skincare",
        "crs_bridal",
    }
    assert PaymentTransaction.objects.filter(student=priya, status="failed", course_id="crs_salon").exists()
    assert Certificate.objects.get(student=priya).certificate_number.endswith("-00118")
    assert CourseEnrollment.objects.get(student=priya, course_id="crs_skincare").completed_at is not None
    assert BatchEnrollment.objects.get(student=priya, batch_id="bt_makeup_3").status == "in_progress"

    assert set(PaymentTransaction.objects.values_list("status", flat=True)) == {"paid", "pending", "failed", "refunded"}
    refunded = PaymentTransaction.objects.get(status="refunded")
    assert CourseEnrollment.objects.get(payment=refunded).access_status == "revoked"
    assert BatchEnrollment.objects.get(student_id="usr_student_7").status == "withdrawn"

    assert Certificate.objects.count() == 9
    revoked = Certificate.objects.get(status="revoked")
    assert revoked.revoked_reason.startswith("Issued in error") and revoked.revoked_at is not None
    assert BatchHistoryEvent.objects.filter(type="certificate_revoked", batch_id="bt_makeup_2").exists()
    assert AuditLog.objects.filter(action="certificate.revoke", entity_id=revoked.id).exists()

    # Live sessions relative to today: one is joinable right now
    now = timezone.now()
    joinable = LiveSession.objects.get(title="Live Practical: Day-to-Night Looks")
    assert joinable.status == "scheduled"
    assert now < joinable.starts_at <= now + timedelta(minutes=6)
    assert joinable.starts_at - timedelta(minutes=joinable.join_window_minutes_before) <= now
    assert LiveSession.objects.filter(status="cancelled").count() == 1
    assert Recording.objects.filter(status="available").count() >= 5
    assert AttendanceRecord.objects.filter(session__batch_id="bt_makeup_3").exists()
    assert LessonProgress.objects.filter(student=priya, course_id="crs_makeup", completed=True).count() == 7

    # Refer & Earn
    config = ReferralProgramConfig.get()
    assert (
        config.enabled
        and config.referrer_reward_amount == 500
        and config.referee_discount_amount == 300
        and config.max_credit_per_order == 2000
    )
    referrals = Referral.objects.filter(referrer=priya)
    assert referrals.count() == 3
    assert referrals.filter(status="rewarded").count() == 1 and referrals.filter(status="signed_up").count() == 2
    assert ReferralReward.objects.filter(user=priya, status="credited").count() == 1
    assert sum(r.amount for r in ReferralReward.objects.filter(user=priya)) == 500
    assert User.objects.get(pk="usr_student_10").referred_by == priya

    assert BatchHistoryEvent.objects.filter(type="batch_created").count() == 6
    assert AuditLog.objects.count() == 8


def test_seed_is_idempotent():
    _run()
    before = _counts()
    priya_id = User.objects.get(email="priya@student.demo").id
    cert_numbers = set(Certificate.objects.values_list("certificate_number", flat=True))
    _run()
    assert _counts() == before
    assert User.objects.get(email="priya@student.demo").id == priya_id
    assert set(Certificate.objects.values_list("certificate_number", flat=True)) == cert_numbers
    assert User.objects.get(email="priya@student.demo").check_password("Student@123")


def test_seed_reset_rebuilds_same_dataset():
    _run()
    before = _counts()
    _run("--reset")
    assert _counts() == before
