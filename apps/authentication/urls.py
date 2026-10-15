"""URL patterns for the authentication app. Paths have no trailing slash (mounted under /api/v1/)."""

from django.urls import path

from apps.authentication import views

urlpatterns = [
    path("auth/register", views.RegisterView.as_view(), name="auth-register"),
    path("auth/resend-otp", views.ResendOtpView.as_view(), name="auth-resend-otp"),
    path("auth/verify-otp", views.VerifyOtpView.as_view(), name="auth-verify-otp"),
    path("auth/login", views.LoginView.as_view(), name="auth-login"),
    path("auth/logout", views.LogoutView.as_view(), name="auth-logout"),
    path("auth/token/refresh", views.TokenRefreshView.as_view(), name="auth-token-refresh"),
    path("auth/me", views.MeView.as_view(), name="auth-me"),
    path("auth/forgot-password", views.ForgotPasswordView.as_view(), name="auth-forgot-password"),
    path("auth/reset-password", views.ResetPasswordView.as_view(), name="auth-reset-password"),
]
