"""Authentication endpoints under /api/v1/auth/*. Views stay thin; rules live in services.py."""

from __future__ import annotations

from django.conf import settings
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.serializers import TokenRefreshSerializer
from rest_framework_simplejwt.tokens import AccessToken

from apps.authentication import services
from apps.authentication.serializers import (
    AuthSessionSerializer,
    EmailSerializer,
    ForgotPasswordResponseSerializer,
    LoginSerializer,
    LogoutSerializer,
    MessageResponseSerializer,
    RegisterResponseSerializer,
    RegisterSerializer,
    ResetPasswordSerializer,
    SuccessResponseSerializer,
    TokenRefreshRequestSerializer,
    TokenRefreshResponseSerializer,
    UserSerializer,
    VerifyOtpSerializer,
)
from apps.authentication.throttles import LoginThrottle, OtpThrottle, PasswordResetThrottle, RegisterThrottle

TAGS = ["authentication"]


class PublicAuthView(APIView):
    """Unauthenticated endpoint: a stale bearer token must never block it."""

    authentication_classes: list = []
    permission_classes = [AllowAny]


class RegisterView(PublicAuthView):
    throttle_classes = [RegisterThrottle]

    @extend_schema(
        tags=TAGS,
        summary="Register a student account",
        request=RegisterSerializer,
        responses={201: RegisterResponseSerializer},
    )
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.register_user(**serializer.validated_data)
        body = RegisterResponseSerializer({"user_id": user.pk, "email": user.email, "verification_required": True})
        return Response(body.data, status=status.HTTP_201_CREATED)


class ResendOtpView(PublicAuthView):
    throttle_classes = [OtpThrottle]

    @extend_schema(
        tags=TAGS, summary="Resend the verification code", request=EmailSerializer, responses=MessageResponseSerializer
    )
    def post(self, request):
        serializer = EmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.resend_otp(serializer.validated_data["email"])
        return Response({"message": services.RESEND_OTP_MESSAGE})


class VerifyOtpView(PublicAuthView):
    throttle_classes = [OtpThrottle]

    @extend_schema(
        tags=TAGS,
        summary="Verify the email OTP and sign in",
        request=VerifyOtpSerializer,
        responses=AuthSessionSerializer,
    )
    def post(self, request):
        serializer = VerifyOtpSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.verify_otp(serializer.validated_data["email"], serializer.validated_data["otp"])
        return Response(AuthSessionSerializer(services.build_session(user)).data)


class LoginView(PublicAuthView):
    throttle_classes = [LoginThrottle]

    @extend_schema(
        tags=TAGS, summary="Sign in with email and password", request=LoginSerializer, responses=AuthSessionSerializer
    )
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.login(request=request, **serializer.validated_data)
        self._reset_throttles(request)
        return Response(AuthSessionSerializer(services.build_session(user)).data)

    def _reset_throttles(self, request) -> None:
        """A successful sign-in clears the failed-attempt counter for this identifier (like the mock)."""
        for throttle in self.get_throttles():
            key = throttle.get_cache_key(request, self)
            if key:
                throttle.cache.delete(key)


class LogoutView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(
        tags=TAGS,
        summary="Sign out (revoke refresh token(s))",
        request=LogoutSerializer,
        responses=SuccessResponseSerializer,
    )
    def post(self, request):
        serializer = LogoutSerializer(data=request.data or {})
        serializer.is_valid(raise_exception=True)
        services.logout(request.user, serializer.validated_data.get("refresh") or None)
        return Response({"success": True})


class TokenRefreshView(PublicAuthView):
    def get_authenticate_header(self, request) -> str:
        # Without a WWW-Authenticate header DRF downgrades InvalidToken (401) to 403.
        return 'Bearer realm="api"'

    @extend_schema(
        tags=TAGS,
        summary="Rotate the refresh token and obtain a new access token",
        request=TokenRefreshRequestSerializer,
        responses=TokenRefreshResponseSerializer,
    )
    def post(self, request):
        serializer = TokenRefreshSerializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except TokenError as exc:
            raise InvalidToken(exc.args[0])
        data = dict(serializer.validated_data)
        data["expires_at"] = services.token_expiry(AccessToken(data["access"]))
        return Response(TokenRefreshResponseSerializer(data).data)


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=TAGS, summary="Current user", responses=UserSerializer)
    def get(self, request):
        return Response(UserSerializer(request.user).data)


class ForgotPasswordView(PublicAuthView):
    throttle_classes = [PasswordResetThrottle]

    @extend_schema(
        tags=TAGS,
        summary="Request a password reset link",
        request=EmailSerializer,
        responses=ForgotPasswordResponseSerializer,
    )
    def post(self, request):
        serializer = EmailSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        raw_token = services.forgot_password(serializer.validated_data["email"])
        body = {"message": services.FORGOT_PASSWORD_MESSAGE}
        if settings.DEBUG and raw_token:
            # Development convenience mirroring the frontend mock. Never emitted when DEBUG is off.
            body["dev_reset_token"] = raw_token
        return Response(body)


class ResetPasswordView(PublicAuthView):
    throttle_classes = [PasswordResetThrottle]

    @extend_schema(
        tags=TAGS,
        summary="Reset the password with an emailed token",
        request=ResetPasswordSerializer,
        responses=MessageResponseSerializer,
    )
    def post(self, request):
        serializer = ResetPasswordSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.reset_password(request=request, **serializer.validated_data)
        return Response({"message": services.RESET_PASSWORD_MESSAGE})
