from datetime import timedelta

import pytest
from django.utils import timezone

from apps.reports.services import MONTH_ABBR

pytestmark = pytest.mark.django_db

OVERVIEW = "/api/v1/admin/overview"
ANALYTICS = "/api/v1/admin/analytics"


def test_overview_numbers(admin_client, academy, batch):
    res = admin_client.get(OVERVIEW)
    assert res.status_code == 200
    body = res.json()
    assert body["totalStudents"] == 3  # admin is not a student
    assert body["activeStudents"] == 1  # Priya: active and not completed
    assert body["totalCourses"] == 3 and body["publishedCourses"] == 2
    assert body["activeBatches"] == 1
    assert body["upcomingLiveClasses"] == 1  # the joinable session; the completed one is excluded
    assert body["paidEnrollments"] == 2
    assert body["totalPaymentsReceived"] == 29999 + 9999
    assert body["pendingPayments"] == 1
    assert body["pendingPaymentAmount"] == 9999
    assert body["completedCourses"] == 1
    assert body["certificatesIssued"] == 1
    assert body["currency"] == "INR"
    assert set(body["range"]) == {"from", "to"}
    assert body["range"]["from"] <= body["range"]["to"]


def test_overview_date_range_filters_time_based_metrics(admin_client, academy):
    today = timezone.localtime().date()
    tomorrow = (today + timedelta(days=1)).isoformat()
    yesterday = (today - timedelta(days=1)).isoformat()

    future = admin_client.get(OVERVIEW, {"from": tomorrow}).json()
    assert future["paidEnrollments"] == 0
    assert future["totalPaymentsReceived"] == 0
    assert future["pendingPayments"] == 0 and future["pendingPaymentAmount"] == 0
    assert future["completedCourses"] == 0
    assert future["certificatesIssued"] == 0
    # Non time-based counters are unaffected by the range.
    assert future["totalStudents"] == 3 and future["activeBatches"] == 1
    assert future["range"]["from"].startswith(tomorrow)

    past = admin_client.get(OVERVIEW, {"to": yesterday}).json()
    assert past["paidEnrollments"] == 0 and past["certificatesIssued"] == 0 and past["pendingPayments"] == 0
    assert past["range"]["to"].startswith(yesterday)

    whole_day = admin_client.get(OVERVIEW, {"from": today.isoformat(), "to": today.isoformat()}).json()
    assert whole_day["paidEnrollments"] == 2
    assert whole_day["totalPaymentsReceived"] == 29999 + 9999
    assert whole_day["certificatesIssued"] == 1
    assert whole_day["completedCourses"] == 1

    res = admin_client.get(OVERVIEW, {"from": "not-a-date"})
    assert res.status_code == 400 and res.json()["code"] == "VALIDATION"


def test_overview_range_echo_defaults(admin_client, academy):
    body = admin_client.get(OVERVIEW).json()
    assert body["range"]["from"] and body["range"]["to"]


def test_analytics(admin_client, academy, course, recorded_course, draft_course):
    res = admin_client.get(ANALYTICS)
    assert res.status_code == 200
    body = res.json()
    months = body["revenueByMonth"]
    assert len(months) == 6
    now = timezone.localtime()
    assert months[-1]["month"] == MONTH_ABBR[now.month - 1]
    assert months[0]["month"] == MONTH_ABBR[(now.month - 6) % 12]
    assert sum(m["revenue"] for m in months) == 29999 + 9999  # paid payments only (pending excluded)
    assert sum(m["enrollments"] for m in months) == 2
    assert months[-1]["enrollments"] == 2  # both enrollments were created today
    paid_month = timezone.localtime(now - timedelta(days=20)).month
    assert next(m for m in months if m["month"] == MONTH_ABBR[paid_month - 1])["revenue"] >= 29999

    by_course = {row["courseId"]: row for row in body["enrollmentsByCourse"]}
    assert draft_course.id not in by_course
    assert by_course[course.id] == {
        "courseId": course.id,
        "courseTitle": course.title,
        "enrollments": 1,
        "completions": 0,
    }
    assert by_course[recorded_course.id] == {
        "courseId": recorded_course.id,
        "courseTitle": recorded_course.title,
        "enrollments": 1,
        "completions": 1,
    }


def test_analytics_excludes_pending_payment_enrollments(admin_client, academy, course, other_student, pending_payment):
    from apps.enrollments.models import CourseEnrollment

    CourseEnrollment.objects.create(
        student=other_student,
        course=course,
        source="purchase",
        access_status="pending_payment",
        payment=pending_payment,
    )
    body = admin_client.get(ANALYTICS).json()
    row = next(r for r in body["enrollmentsByCourse"] if r["courseId"] == course.id)
    assert row["enrollments"] == 1


@pytest.mark.parametrize("path", [OVERVIEW, ANALYTICS])
def test_reports_permissions(api, student_client, path):
    assert api.get(path).status_code == 401
    forbidden = student_client.get(path)
    assert forbidden.status_code == 403 and forbidden.json()["code"] == "FORBIDDEN"
