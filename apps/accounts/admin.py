"""Minimal Django admin registration for the custom user model (dev/ops convenience only)."""

from django.contrib import admin

from apps.accounts.models import LoginAttempt, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "name", "role", "email_verified", "is_active", "created_at")
    list_filter = ("role", "email_verified", "is_active")
    search_fields = ("email", "name", "mobile", "referral_code")
    ordering = ("-created_at",)
    readonly_fields = ("id", "referral_code", "created_at", "updated_at", "last_login")
    exclude = ("password", "groups", "user_permissions")  # hashes are never edited through the admin


@admin.register(LoginAttempt)
class LoginAttemptAdmin(admin.ModelAdmin):
    list_display = ("email", "successful", "reason", "ip_address", "created_at")
    list_filter = ("successful",)
    search_fields = ("email",)
    readonly_fields = ("email", "ip_address", "user_agent", "successful", "reason", "created_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False
