"""URL patterns for the courses app. Paths have no trailing slash and are mounted under /api/v1/."""

from django.urls import path

from apps.courses import views

urlpatterns = [
    # Public catalogue
    path("courses", views.PublicCourseListView.as_view(), name="public-course-list"),
    path("courses/<str:id_or_slug>", views.PublicCourseDetailView.as_view(), name="public-course-detail"),
    path("courses/<str:id_or_slug>/sessions", views.PublicCourseSessionsView.as_view(), name="public-course-sessions"),
    path("trainers", views.PublicTrainerListView.as_view(), name="public-trainer-list"),
    path("academy/stats", views.AcademyStatsView.as_view(), name="academy-stats"),
    # Admin
    path("admin/courses", views.AdminCourseListCreateView.as_view(), name="admin-course-list"),
    path("admin/courses/<str:course_id>", views.AdminCourseDetailView.as_view(), name="admin-course-detail"),
    path("admin/courses/<str:course_id>/publish", views.AdminCoursePublishView.as_view(), name="admin-course-publish"),
    path(
        "admin/courses/<str:course_id>/unpublish",
        views.AdminCourseUnpublishView.as_view(),
        name="admin-course-unpublish",
    ),
    path(
        "admin/courses/<str:course_id>/modules",
        views.AdminCourseCurriculumView.as_view(),
        name="admin-course-curriculum",
    ),
    path(
        "admin/courses/<str:course_id>/thumbnail",
        views.AdminCourseThumbnailView.as_view(),
        name="admin-course-thumbnail",
    ),
    path(
        "admin/courses/<str:course_id>/enrolled-students",
        views.AdminCourseEnrolledStudentsView.as_view(),
        name="admin-course-enrolled-students",
    ),
    path("admin/trainers", views.AdminTrainerListCreateView.as_view(), name="admin-trainer-list"),
    path("admin/trainers/<str:trainer_id>", views.AdminTrainerDetailView.as_view(), name="admin-trainer-detail"),
]
