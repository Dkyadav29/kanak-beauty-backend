"""URL patterns for the batches app. Paths have no trailing slash and are mounted under /api/v1/."""

from django.urls import path

from apps.batches import views

urlpatterns = [
    # Public
    path("live-sessions/upcoming", views.UpcomingLiveSessionsView.as_view(), name="live-sessions-upcoming"),
    # Student
    path("student/live-sessions", views.StudentLiveSessionsView.as_view(), name="student-live-sessions"),
    path(
        "student/live-sessions/<str:session_id>/join",
        views.JoinLiveSessionView.as_view(),
        name="student-live-session-join",
    ),
    path("student/recordings", views.StudentRecordingsView.as_view(), name="student-recordings"),
    path(
        "student/recordings/<str:recording_id>/playback",
        views.RecordingPlaybackView.as_view(),
        name="student-recording-playback",
    ),
    path("media/recordings/<str:token>", views.RecordingMediaView.as_view(), name="media-recording-stream"),
    # Admin: batches
    path("admin/batches", views.AdminBatchListCreateView.as_view(), name="admin-batches"),
    path("admin/batches/<str:batch_id>", views.AdminBatchDetailView.as_view(), name="admin-batch-detail"),
    path("admin/batches/<str:batch_id>/status", views.AdminBatchStatusView.as_view(), name="admin-batch-status"),
    path("admin/batches/<str:batch_id>/students", views.AdminBatchStudentsView.as_view(), name="admin-batch-students"),
    path(
        "admin/batches/<str:batch_id>/students/<str:student_id>/withdraw",
        views.AdminBatchWithdrawStudentView.as_view(),
        name="admin-batch-withdraw-student",
    ),
    # Admin: live sessions
    path("admin/live-sessions", views.AdminLiveSessionListCreateView.as_view(), name="admin-live-sessions"),
    path(
        "admin/live-sessions/<str:session_id>/cancel",
        views.AdminLiveSessionCancelView.as_view(),
        name="admin-live-session-cancel",
    ),
    path(
        "admin/live-sessions/<str:session_id>/attendance",
        views.AdminLiveSessionAttendanceView.as_view(),
        name="admin-live-session-attendance",
    ),
    path(
        "admin/live-sessions/<str:session_id>/recording",
        views.AdminLiveSessionRecordingView.as_view(),
        name="admin-live-session-recording",
    ),
]
