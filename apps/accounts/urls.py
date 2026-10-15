"""URL patterns for the accounts app. Paths have no trailing slash (mounted under /api/v1/)."""

from django.urls import path

from apps.accounts import views

urlpatterns = [
    path("student/profile", views.StudentProfileView.as_view(), name="student-profile"),
    path("student/change-password", views.ChangePasswordView.as_view(), name="student-change-password"),
]
