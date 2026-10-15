"""URL patterns for the enrollments app. Paths have no trailing slash and are mounted under /api/v1/."""

from django.urls import path

from apps.enrollments import views

urlpatterns = [
    path("student/dashboard", views.StudentDashboardView.as_view(), name="student-dashboard"),
    path("student/enrollments", views.StudentEnrollmentListView.as_view(), name="student-enrollments"),
    path("student/courses/<str:course_id>", views.StudentCourseView.as_view(), name="student-course"),
    path(
        "student/courses/<str:course_id>/progress",
        views.StudentCourseProgressView.as_view(),
        name="student-course-progress",
    ),
    path(
        "student/courses/<str:course_id>/lessons/<str:lesson_id>/progress",
        views.StudentLessonProgressView.as_view(),
        name="student-lesson-progress",
    ),
    path("admin/enrollments", views.AdminEnrollmentListView.as_view(), name="admin-enrollments"),
    path("admin/enrollments/grant", views.AdminEnrollmentGrantView.as_view(), name="admin-enrollment-grant"),
    path(
        "admin/enrollments/<str:enrollment_id>/access",
        views.AdminEnrollmentAccessView.as_view(),
        name="admin-enrollment-access",
    ),
]
