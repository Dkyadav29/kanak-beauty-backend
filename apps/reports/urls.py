"""URL patterns for the reports app. Paths have no trailing slash."""

from django.urls import path

from . import views

urlpatterns = [
    path("admin/overview", views.AdminOverviewView.as_view(), name="admin-overview"),
    path("admin/analytics", views.AdminAnalyticsView.as_view(), name="admin-analytics"),
    path("admin/students", views.AdminStudentListView.as_view(), name="admin-students"),
    path("admin/students/<str:student_id>", views.AdminStudentDetailView.as_view(), name="admin-student-detail"),
]
