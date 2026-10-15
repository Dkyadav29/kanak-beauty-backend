"""
Centralised role -> permission vocabulary and DRF permission classes.

This mirrors the frontend's permission strings (src/types/auth.ts). The
backend is the source of truth: every protected view declares the permission it
requires, and ownership of ``:self`` resources is enforced in querysets/services.
"""

from __future__ import annotations

from rest_framework.permissions import BasePermission

ROLE_STUDENT = "student"
ROLE_ADMIN = "admin"
ROLES = (ROLE_STUDENT, ROLE_ADMIN)

ROLE_PERMISSIONS: dict[str, tuple[str, ...]] = {
    ROLE_STUDENT: (
        "profile:read:self",
        "profile:update:self",
        "courses:read:enrolled",
        "live:join",
        "recordings:watch",
        "progress:update:self",
        "payments:read:self",
        "certificates:read:self",
        "referrals:read:self",
    ),
    ROLE_ADMIN: (
        "analytics:read",
        "students:read",
        "students:manage",
        "courses:manage",
        "batches:manage",
        "enrollments:read",
        "enrollments:manage",
        "payments:read",
        "payments:reconcile",
        "certificates:manage",
        "referrals:manage",
        "audit:read",
    ),
}


def permissions_for_role(role: str) -> list[str]:
    return list(ROLE_PERMISSIONS.get(role, ()))


def user_has_permission(user, permission: str) -> bool:
    if not user or not user.is_authenticated or not user.is_active:
        return False
    return permission in ROLE_PERMISSIONS.get(getattr(user, "role", ""), ())


class IsStudent(BasePermission):
    message = "Only student accounts can access this resource."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_active and u.role == ROLE_STUDENT)


class IsAdmin(BasePermission):
    message = "You do not have permission to access this resource."

    def has_permission(self, request, view):
        u = request.user
        return bool(u and u.is_authenticated and u.is_active and u.role == ROLE_ADMIN)


class HasPermission(BasePermission):
    """
    Usage::

        class MyView(APIView):
            permission_classes = [HasPermission]
            required_permission = "courses:manage"          # or a tuple -> all required

    Views may also expose ``required_permissions_by_action`` for ViewSets.
    """

    message = "You do not have permission to perform this action."

    def has_permission(self, request, view):
        required = getattr(view, "required_permission", None)
        by_action = getattr(view, "required_permissions_by_action", None)
        if by_action and getattr(view, "action", None):
            required = by_action.get(view.action, required)
        if required is None:
            return bool(request.user and request.user.is_authenticated)
        if isinstance(required, str):
            required = (required,)
        return all(user_has_permission(request.user, p) for p in required)


class IsOwnerOrAdmin(BasePermission):
    """Object-level check: object.student/user must be the caller unless the caller is an admin."""

    def has_object_permission(self, request, view, obj):
        u = request.user
        if u.role == ROLE_ADMIN:
            return True
        owner = getattr(obj, "student", None) or getattr(obj, "user", None) or getattr(obj, "referrer", None)
        owner_id = getattr(owner, "id", owner)
        return owner_id == u.id
