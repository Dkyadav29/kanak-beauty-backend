"""URL patterns for the certificates app. Paths have no trailing slash."""

from django.urls import path

from . import views

urlpatterns = [
    # Public
    path("certificates/verify/<str:number>", views.CertificateVerifyView.as_view(), name="certificate-verify"),
    # Student
    path("student/certificates", views.StudentCertificateListView.as_view(), name="student-certificates"),
    path(
        "student/certificates/<str:certificate_id>",
        views.StudentCertificateDetailView.as_view(),
        name="student-certificate-detail",
    ),
    # Admin
    path("admin/certificates", views.AdminCertificateListView.as_view(), name="admin-certificates"),
    path("admin/certificates/eligible/list", views.EligibleStudentsView.as_view(), name="admin-certificates-eligible"),
    path("admin/certificates/issue", views.IssueCertificateView.as_view(), name="admin-certificates-issue"),
    path(
        "admin/certificates/<str:certificate_id>",
        views.AdminCertificateDetailView.as_view(),
        name="admin-certificate-detail",
    ),
    path(
        "admin/certificates/<str:certificate_id>/revoke",
        views.RevokeCertificateView.as_view(),
        name="admin-certificate-revoke",
    ),
    path(
        "admin/certificates/<str:certificate_id>/reissue",
        views.ReissueCertificateView.as_view(),
        name="admin-certificate-reissue",
    ),
]
