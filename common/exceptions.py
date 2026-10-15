"""
Consistent error envelope: ``{ "code": str, "message": str, "details"?: {field: [msg]} }``.

Matches ApiErrorPayload in the frontend. Internal errors never leak stack
traces; they are logged server-side and reported as a generic message.
"""

from __future__ import annotations

import logging

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import set_rollback

logger = logging.getLogger("kanak.api")


class ApiError(exceptions.APIException):
    """Raise from services/views with a stable machine-readable code."""

    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "BAD_REQUEST"
    default_detail = "The request was invalid."

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict | None = None,
    ):
        self.message = message or self.default_detail
        self.code = code or self.default_code
        if status_code is not None:
            self.status_code = status_code
        self.details = details
        super().__init__(self.message, self.code)


class ConflictError(ApiError):
    status_code = status.HTTP_409_CONFLICT
    default_code = "CONFLICT"
    default_detail = "This action conflicts with the current state."


class ForbiddenError(ApiError):
    status_code = status.HTTP_403_FORBIDDEN
    default_code = "FORBIDDEN"
    default_detail = "You do not have permission to perform this action."


class NotFoundError(ApiError):
    status_code = status.HTTP_404_NOT_FOUND
    default_code = "NOT_FOUND"
    default_detail = "The requested resource was not found."


class ValidationFailed(ApiError):
    status_code = status.HTTP_400_BAD_REQUEST
    default_code = "VALIDATION"
    default_detail = "Validation failed."


_DEFAULT_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHENTICATED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    415: "UNSUPPORTED_MEDIA_TYPE",
    429: "RATE_LIMITED",
}


def _flatten_validation(detail) -> dict[str, list[str]]:
    """Turn DRF's nested ValidationError detail into {field: [messages]}."""
    out: dict[str, list[str]] = {}
    if isinstance(detail, dict):
        for key, value in detail.items():
            if isinstance(value, dict):
                for sub_key, sub_val in _flatten_validation(value).items():
                    out[f"{key}.{sub_key}"] = sub_val
            elif isinstance(value, (list, tuple)):
                out[str(key)] = [str(v) for v in value]
            else:
                out[str(key)] = [str(value)]
    elif isinstance(detail, (list, tuple)):
        out["non_field_errors"] = [str(v) for v in detail]
    else:
        out["non_field_errors"] = [str(detail)]
    return out


def api_exception_handler(exc, context):
    # Translate Django exceptions into DRF ones first.
    if isinstance(exc, Http404):
        exc = exceptions.NotFound()
    elif isinstance(exc, DjangoPermissionDenied):
        exc = exceptions.PermissionDenied()
    elif isinstance(exc, DjangoValidationError):
        exc = exceptions.ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages)
    elif isinstance(exc, IntegrityError):
        logger.warning("Integrity error: %s", exc.__class__.__name__)
        exc = ConflictError("The request conflicts with existing data.", code="INTEGRITY_CONFLICT")

    if isinstance(exc, ApiError):
        set_rollback()
        body = {"code": exc.code, "message": exc.message}
        if exc.details:
            body["details"] = exc.details
        return Response(body, status=exc.status_code)

    if isinstance(exc, exceptions.ValidationError):
        set_rollback()
        details = _flatten_validation(exc.detail)
        first = next(iter(details.values()), ["Validation failed."])[0]
        return Response({"code": "VALIDATION", "message": first, "details": details}, status=400)

    if isinstance(exc, exceptions.Throttled):
        set_rollback()
        wait = int(exc.wait or 0)
        return Response(
            {
                "code": "RATE_LIMITED",
                "message": f"Too many attempts. Please try again in {max(1, wait // 60)} minute(s).",
            },
            status=429,
            headers={"Retry-After": str(wait)} if wait else None,
        )

    if isinstance(exc, exceptions.APIException):
        set_rollback()
        detail = exc.detail
        code = getattr(detail, "code", None) if not isinstance(detail, (dict, list)) else None
        message = str(detail) if not isinstance(detail, (dict, list)) else exc.default_detail
        if isinstance(exc, exceptions.NotAuthenticated) or isinstance(exc, exceptions.AuthenticationFailed):
            message = (
                "Your session has expired. Please sign in again." if code in ("token_not_valid", None) else message
            )
            code = (
                "UNAUTHENTICATED"
                if code in (None, "not_authenticated", "token_not_valid", "authentication_failed")
                else code
            )
        mapped = _DEFAULT_CODES.get(exc.status_code, "ERROR")
        # DRF's generic codes map onto the contract's vocabulary; custom codes pass through upper-cased.
        generic = {
            None,
            "error",
            "invalid",
            "permission_denied",
            "not_found",
            "method_not_allowed",
            "throttled",
            "parse_error",
            "unsupported_media_type",
            "not_acceptable",
        }
        body = {"code": mapped if code in generic else str(code).upper(), "message": message}
        headers = getattr(exc, "auth_header", None)
        return Response(body, status=exc.status_code, headers={"WWW-Authenticate": headers} if headers else None)

    # Unhandled: log with traceback server-side, return a generic message.
    logger.exception(
        "Unhandled API error in %s", context.get("view").__class__.__name__ if context.get("view") else "view"
    )
    set_rollback()
    return Response({"code": "INTERNAL", "message": "An unexpected error occurred."}, status=500)
