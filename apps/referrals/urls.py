"""URL patterns for the referrals app. Paths have no trailing slash and are mounted under /api/v1/."""

from django.urls import path

from apps.referrals import views

urlpatterns = [
    path("referrals/validate/<str:code>", views.ValidateReferralCodeView.as_view(), name="referrals-validate"),
    path("student/referrals", views.StudentReferralsView.as_view(), name="student-referrals"),
    path("admin/referrals", views.AdminReferralListView.as_view(), name="admin-referrals"),
    path("admin/referrals/stats", views.AdminReferralStatsView.as_view(), name="admin-referrals-stats"),
    path("admin/referrals/config", views.AdminReferralConfigView.as_view(), name="admin-referrals-config"),
]
