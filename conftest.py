"""
Shared pytest fixtures. Tests use MariaDB (config.settings.test) unless
TEST_DB_SQLITE=1. Fixtures build realistic domain data through the ORM.
"""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

STUDENT_PASSWORD = "Student@123"
ADMIN_PASSWORD = "Admin@123"


@pytest.fixture
def api() -> APIClient:
    return APIClient()


@pytest.fixture
def admin_user(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="admin@kanakacademy.test",
        password=ADMIN_PASSWORD,
        name="Kanak Admin",
        mobile="+91 98000 00001",
        role="admin",
        email_verified=True,
        is_staff=True,
    )


@pytest.fixture
def student(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="priya@student.test",
        password=STUDENT_PASSWORD,
        name="Priya Sharma",
        mobile="+91 98111 22334",
        email_verified=True,
    )


@pytest.fixture
def other_student(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="ananya@student.test",
        password=STUDENT_PASSWORD,
        name="Ananya Verma",
        mobile="+91 98222 33445",
        email_verified=True,
    )


@pytest.fixture
def unverified_student(db):
    from apps.accounts.models import User

    return User.objects.create_user(
        email="zoya@student.test",
        password=STUDENT_PASSWORD,
        name="Zoya Ali",
        mobile="+91 97222 33445",
        email_verified=False,
    )


def _auth(client: APIClient, user) -> APIClient:
    from rest_framework_simplejwt.tokens import RefreshToken

    token = RefreshToken.for_user(user).access_token
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
    return client


@pytest.fixture
def student_client(student) -> APIClient:
    return _auth(APIClient(), student)


@pytest.fixture
def other_student_client(other_student) -> APIClient:
    return _auth(APIClient(), other_student)


@pytest.fixture
def admin_client(admin_user) -> APIClient:
    return _auth(APIClient(), admin_user)


@pytest.fixture
def auth_client():
    """Factory: auth_client(user) -> APIClient authenticated as that user."""

    def _make(user):
        return _auth(APIClient(), user)

    return _make


@pytest.fixture
def trainer(db):
    from apps.courses.models import Trainer

    return Trainer.objects.create(
        name="Meera Kapoor",
        title="Lead Makeup Educator",
        bio="Bridal and editorial artist.",
        specialities=["Bridal Makeup", "HD"],
    )


@pytest.fixture
def course(db, trainer):
    """Published hybrid course with 2 modules / 5 lessons incl. a live and an assessment lesson."""
    from apps.courses.models import Course, CourseModule, Lesson

    c = Course.objects.create(
        title="Professional Makeup Artistry",
        short_description="Master base work, eyes and client consultation.",
        description="A comprehensive programme covering skin preparation, colour theory and client-ready looks.",
        thumbnail_url="https://example.test/makeup.jpg",
        trainer=trainer,
        duration_weeks=8,
        total_hours=48,
        level="beginner",
        format="hybrid",
        price=Decimal("34999.00"),
        discount_price=Decimal("29999.00"),
        status="published",
        featured=True,
        certificate_enabled=True,
        certificate_issuance="automatic",
        require_all_required_lessons=True,
        min_live_attendance_percent=75,
        require_assessment=True,
        prerequisites=["None"],
        outcomes=["Match foundation", "Build a portfolio"],
        schedule_summary="Live practicals twice a week.",
    )
    m1 = CourseModule.objects.create(course=c, title="Foundations", description="Tools and prep", order=1)
    m2 = CourseModule.objects.create(course=c, title="Client-ready looks", description="Bringing it together", order=2)
    Lesson.objects.create(
        module=m1, title="Welcome & Kit", type="video", duration_minutes=18, order=1, summary="Kit walkthrough"
    )
    Lesson.objects.create(
        module=m1, title="Colour Theory", type="reading", duration_minutes=20, order=2, summary="Undertones"
    )
    Lesson.objects.create(
        module=m1,
        title="Live Practical: Base Work",
        type="live",
        duration_minutes=120,
        order=3,
        summary="Live base work",
    )
    Lesson.objects.create(
        module=m2, title="Final Assessment", type="assessment", duration_minutes=45, order=1, summary="Graded"
    )
    Lesson.objects.create(
        module=m2,
        title="Bonus: Social Media",
        type="video",
        duration_minutes=22,
        order=2,
        is_required=False,
        summary="Optional",
    )
    return c


@pytest.fixture
def recorded_course(db, trainer):
    """Published self-paced course: 2 required video lessons, no attendance/assessment."""
    from apps.courses.models import Course, CourseModule, Lesson

    c = Course.objects.create(
        title="Salon Management and Business Skills",
        short_description="Pricing, retention and marketing for salon owners.",
        description="A business programme for beauty professionals who want to run profitable salons.",
        trainer=trainer,
        duration_weeks=4,
        total_hours=16,
        level="intermediate",
        format="recorded",
        price=Decimal("12999.00"),
        discount_price=Decimal("9999.00"),
        status="published",
        certificate_enabled=True,
        certificate_issuance="automatic",
        require_all_required_lessons=True,
        min_live_attendance_percent=None,
        require_assessment=False,
    )
    m = CourseModule.objects.create(course=c, title="Running the numbers", order=1)
    Lesson.objects.create(module=m, title="Pricing Strategy", type="video", duration_minutes=32, order=1)
    Lesson.objects.create(module=m, title="Inventory & Retail", type="video", duration_minutes=28, order=2)
    return c


@pytest.fixture
def draft_course(db, trainer):
    from apps.courses.models import Course

    return Course.objects.create(
        title="Airbrush Intensive",
        short_description="Draft course not yet published.",
        description="Draft course currently being prepared by the academy team.",
        trainer=trainer,
        duration_weeks=2,
        total_hours=12,
        level="advanced",
        format="live",
        price=Decimal("15999.00"),
        status="draft",
    )


@pytest.fixture
def batch(db, course, trainer):
    from apps.batches.models import Batch

    today = date.today()
    return Batch.objects.create(
        code="KBA-MK-03",
        name="Makeup Artistry - Batch 3",
        course=course,
        trainer=trainer,
        start_date=today - timedelta(days=30),
        end_date=today + timedelta(days=30),
        capacity=15,
        status="active",
        schedule_note="Tue & Thu, 10:00-12:00",
    )


@pytest.fixture
def enrollment(db, student, course, batch):
    """Active purchased enrollment for `student` in `course`, member of `batch`."""
    from apps.batches.models import BatchEnrollment
    from apps.enrollments.models import CourseEnrollment
    from apps.payments.models import PaymentTransaction

    payment = PaymentTransaction.objects.create(
        order_id="KBA-ORD-1001",
        student=student,
        course=course,
        amount=course.effective_price,
        list_price=course.effective_price,
        status="paid",
        method="UPI",
        gateway_provider="sandbox",
        gateway_reference="gw_test_1001",
        checkout_token="chk_test_1001",
        invoice_available=True,
        paid_at=timezone.now() - timedelta(days=20),
    )
    enr = CourseEnrollment.objects.create(
        student=student, course=course, batch=batch, source="purchase", access_status="active", payment=payment
    )
    BatchEnrollment.objects.create(batch=batch, student=student, status="in_progress")
    return enr


@pytest.fixture
def joinable_session(db, course, batch, trainer):
    """Live session starting in 5 minutes with a 15-minute join window (joinable now)."""
    from apps.batches.models import LiveSession

    return LiveSession.objects.create(
        course=course,
        batch=batch,
        title="Live Practical: Base Work",
        trainer=trainer,
        starts_at=timezone.now() + timedelta(minutes=5),
        duration_minutes=120,
        status="scheduled",
        join_window_minutes_before=15,
        description="Foundation matching on live models.",
    )


@pytest.fixture
def future_session(db, course, batch, trainer):
    from apps.batches.models import LiveSession

    return LiveSession.objects.create(
        course=course,
        batch=batch,
        title="Live Practical: Portfolio Shoot",
        trainer=trainer,
        starts_at=timezone.now() + timedelta(days=7),
        duration_minutes=150,
        status="scheduled",
    )


@pytest.fixture
def completed_session_with_recording(db, course, batch, trainer):
    from apps.batches.models import LiveSession, Recording

    session = LiveSession.objects.create(
        course=course,
        batch=batch,
        title="Live Practical: Evening Eye Look",
        trainer=trainer,
        starts_at=timezone.now() - timedelta(days=14),
        duration_minutes=120,
        status="completed",
    )
    Recording.objects.create(
        session=session,
        course=course,
        title=session.title,
        recorded_at=session.starts_at,
        duration_minutes=120,
        status="available",
        storage_key="recordings/eye-look.mp4",
    )
    return session


@pytest.fixture
def referral_config(db):
    from apps.referrals.models import ReferralProgramConfig

    return ReferralProgramConfig.get()
