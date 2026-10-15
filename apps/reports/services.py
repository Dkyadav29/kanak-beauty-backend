"""Report computations: aggregate queries mirroring the frontend mock's admin handlers."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.db.models import (
    CharField,
    Count,
    Exists,
    F,
    OuterRef,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce, Replace
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.accounts.models import User
from apps.batches.models import AttendanceRecord, Batch, BatchEnrollment, LiveSession
from apps.certificates.models import Certificate
from apps.courses.models import Course
from apps.enrollments.models import CourseEnrollment
from apps.payments.models import PaymentTransaction
from common.exceptions import ValidationFailed

MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
ZERO = Decimal("0.00")


# ---------------------------------------------------------------------------
# Date range helpers
# ---------------------------------------------------------------------------
def _aware(dt: datetime) -> datetime:
    return dt if timezone.is_aware(dt) else timezone.make_aware(dt, timezone.get_current_timezone())


def parse_range_bound(value: str | None, *, field: str, end: bool) -> datetime | None:
    """
    Accepts ``YYYY-MM-DD`` or an ISO datetime. ``from`` is inclusive from that moment
    (start of day for dates); ``to`` is inclusive through the end of that calendar
    day, like the mock's ``iso.slice(0, 10) <= to`` comparison.
    """
    if not value:
        return None
    raw = str(value).strip()
    parsed_dt = parse_datetime(raw)
    parsed_date = parse_date(raw) if parsed_dt is None else None
    if parsed_dt is None and parsed_date is None:
        raise ValidationFailed(
            f"Invalid date for '{field}'.", details={field: ["Use YYYY-MM-DD or an ISO-8601 datetime."]}
        )
    if end:
        day: date = parsed_date or timezone.localtime(_aware(parsed_dt)).date()
        return _aware(datetime.combine(day + timedelta(days=1), time.min))
    if parsed_date is not None:
        return _aware(datetime.combine(parsed_date, time.min))
    return _aware(parsed_dt)


def _range_q(field: str, start: datetime | None, end_exclusive: datetime | None) -> Q:
    q = Q()
    if start is not None:
        q &= Q(**{f"{field}__gte": start})
    if end_exclusive is not None:
        q &= Q(**{f"{field}__lt": end_exclusive})
    return q


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------
def overview_stats(*, from_value: str | None, to_value: str | None) -> dict:
    start = parse_range_bound(from_value, field="from", end=False)
    end = parse_range_bound(to_value, field="to", end=True)
    now = timezone.now()

    payments = PaymentTransaction.objects.filter(_range_q("created_at", start, end))
    payment_totals = payments.aggregate(
        paid_amount=Coalesce(Sum("amount", filter=Q(status="paid")), ZERO),
        pending_count=Count("id", filter=Q(status="pending")),
        pending_amount=Coalesce(Sum("amount", filter=Q(status="pending")), ZERO),
    )

    if start is not None:
        range_from = start
    else:
        first_payment = PaymentTransaction.objects.order_by("created_at").values_list("created_at", flat=True).first()
        range_from = first_payment or now
    range_to = (end - timedelta(microseconds=1)) if end is not None else now

    return {
        "total_students": User.objects.filter(role="student").count(),
        "active_students": CourseEnrollment.objects.filter(access_status="active", completed_at__isnull=True)
        .values("student_id")
        .distinct()
        .count(),
        "total_courses": Course.objects.count(),
        "published_courses": Course.objects.filter(status="published").count(),
        "active_batches": Batch.objects.filter(status="active").count(),
        "upcoming_live_classes": LiveSession.objects.filter(status="scheduled", starts_at__gt=now).count(),
        "paid_enrollments": CourseEnrollment.objects.filter(source="purchase", access_status="active")
        .filter(_range_q("enrolled_at", start, end))
        .count(),
        "total_payments_received": payment_totals["paid_amount"],
        "pending_payments": payment_totals["pending_count"],
        "pending_payment_amount": payment_totals["pending_amount"],
        "completed_courses": CourseEnrollment.objects.filter(completed_at__isnull=False)
        .filter(_range_q("completed_at", start, end))
        .count(),
        "certificates_issued": Certificate.objects.filter(status="issued")
        .filter(_range_q("issued_at", start, end))
        .count(),
        "currency": "INR",
        "range": {"from": range_from, "to": range_to},
    }


# ---------------------------------------------------------------------------
# Analytics
# ---------------------------------------------------------------------------
def _month_key(dt: datetime) -> tuple[int, int]:
    local = timezone.localtime(dt)
    return (local.year, local.month)


def analytics() -> dict:
    today = timezone.localtime().date()
    first_of_month = today.replace(day=1)
    months: list[tuple[int, int]] = []
    y, m = first_of_month.year, first_of_month.month - 5
    while m <= 0:
        m += 12
        y -= 1
    for _ in range(6):
        months.append((y, m))
        m += 1
        if m > 12:
            m = 1
            y += 1
    window_start = _aware(datetime.combine(date(months[0][0], months[0][1], 1), time.min))
    buckets = {key: {"revenue": Decimal("0.00"), "enrollments": 0} for key in months}

    paid = PaymentTransaction.objects.filter(status="paid").filter(
        Q(paid_at__gte=window_start) | Q(paid_at__isnull=True, created_at__gte=window_start)
    )
    for paid_at, created_at, amount in paid.values_list("paid_at", "created_at", "amount"):
        key = _month_key(paid_at or created_at)
        if key in buckets:
            buckets[key]["revenue"] += amount

    for enrolled_at in CourseEnrollment.objects.filter(enrolled_at__gte=window_start).values_list(
        "enrolled_at", flat=True
    ):
        key = _month_key(enrolled_at)
        if key in buckets:
            buckets[key]["enrollments"] += 1

    revenue_by_month = [
        {
            "month": MONTH_ABBR[key[1] - 1],
            "revenue": buckets[key]["revenue"],
            "enrollments": buckets[key]["enrollments"],
        }
        for key in months
    ]

    courses = (
        Course.objects.exclude(status="draft")
        .annotate(
            enrollment_count=Count(
                "enrollments", filter=~Q(enrollments__access_status="pending_payment"), distinct=True
            ),
            completion_count=Count("enrollments", filter=Q(enrollments__completed_at__isnull=False), distinct=True),
        )
        .order_by("created_at", "id")
    )
    enrollments_by_course = [
        {
            "course_id": c.id,
            "course_title": c.title,
            "enrollments": c.enrollment_count,
            "completions": c.completion_count,
        }
        for c in courses
    ]
    return {"revenue_by_month": revenue_by_month, "enrollments_by_course": enrollments_by_course}


# ---------------------------------------------------------------------------
# Student directory
# ---------------------------------------------------------------------------
PAYMENT_STATUSES = {choice for choice, _ in PaymentTransaction.STATUS_CHOICES}


def student_directory(
    *,
    search: str | None = None,
    course_id: str | None = None,
    batch_id: str | None = None,
    payment_status: str | None = None,
    enrollment_status: str | None = None,
):
    last_payment = (
        PaymentTransaction.objects.filter(student=OuterRef("pk")).order_by("-created_at").values("status")[:1]
    )
    qs = User.objects.filter(role="student").annotate(
        enrolled_courses=Count("enrollments", filter=~Q(enrollments__access_status="pending_payment"), distinct=True),
        completed_courses=Count(
            "enrollments",
            filter=~Q(enrollments__access_status="pending_payment") & Q(enrollments__completed_at__isnull=False),
            distinct=True,
        ),
        certificate_count=Count("certificates", filter=Q(certificates__status="issued"), distinct=True),
        last_payment_status=Coalesce(
            Subquery(last_payment, output_field=CharField()), Value("none"), output_field=CharField()
        ),
    )

    if search:
        q = search.strip()
        compact = q.replace(" ", "")
        qs = qs.annotate(mobile_compact=Replace(F("mobile"), Value(" "), Value(""))).filter(
            Q(name__icontains=q) | Q(email__icontains=q) | Q(mobile_compact__icontains=compact)
        )
    if course_id:
        qs = qs.filter(Exists(CourseEnrollment.objects.filter(student=OuterRef("pk"), course_id=course_id)))
    if batch_id:
        qs = qs.filter(Exists(BatchEnrollment.objects.filter(student=OuterRef("pk"), batch_id=batch_id)))
    if payment_status:
        if payment_status not in PAYMENT_STATUSES and payment_status != "none":
            raise ValidationFailed("Invalid paymentStatus.", details={"paymentStatus": ["Unknown payment status."]})
        qs = qs.filter(last_payment_status=payment_status)
    if enrollment_status:
        if enrollment_status == "none":
            qs = qs.filter(enrolled_courses=0)
        elif enrollment_status == "completed":
            qs = qs.filter(completed_courses__gt=0)
        elif enrollment_status == "active":
            qs = qs.filter(enrolled_courses__gt=F("completed_courses"))
        else:
            raise ValidationFailed(
                "Invalid enrollmentStatus.", details={"enrollmentStatus": ["Use active, completed, none or all."]}
            )
    return qs.order_by("-created_at", "-id")


# ---------------------------------------------------------------------------
# Student detail
# ---------------------------------------------------------------------------
def attendance_percent(student, course, batch) -> int | None:
    """Share of the course's completed live sessions (batch-scoped when the student has a batch) attended."""
    sessions = LiveSession.objects.filter(course=course, status="completed")
    if batch is not None:
        sessions = sessions.filter(batch=batch)
    total = sessions.count()
    if total == 0:
        return None
    present = AttendanceRecord.objects.filter(
        session__in=sessions, student=student, status__in=["present", "late"]
    ).count()
    return round(present / total * 100)


def student_detail(student) -> dict:
    from apps.enrollments import services as enrollment_services

    enrollments = (
        CourseEnrollment.objects.filter(student=student).select_related("course", "batch").order_by("-enrolled_at")
    )
    enrollment_rows = []
    for enrollment in enrollments:
        progress = enrollment_services.compute_progress(student, enrollment.course)
        enrollment_rows.append(
            {
                "enrollment_id": enrollment.id,
                "course_id": enrollment.course_id,
                "course_title": enrollment.course.title,
                "batch_name": enrollment.batch.name if enrollment.batch is not None else None,
                "access_status": enrollment.access_status,
                "progress_percent": progress.progress_percent,
                "enrolled_at": enrollment.enrolled_at,
                "completed_at": enrollment.completed_at,
                "attendance_percent": attendance_percent(student, enrollment.course, enrollment.batch),
            }
        )
    return {
        "profile": student,
        "enrollments": enrollment_rows,
        "payments": PaymentTransaction.objects.filter(student=student).select_related("course").order_by("-created_at"),
        "certificates": Certificate.objects.filter(student=student).order_by("-issued_at"),
    }
