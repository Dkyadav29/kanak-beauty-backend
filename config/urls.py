"""
Root URL configuration.

All business endpoints live under /api/v1/ and are declared by each app's
urls.py. Paths deliberately have NO trailing slash to match the existing
frontend contract (docs/API_CONTRACTS.md), e.g. /api/v1/auth/login.
"""

from django.conf import settings
from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView, SpectacularSwaggerView


def healthcheck(_request):
    return JsonResponse({"status": "ok"})


api_v1 = [
    path("health", healthcheck, name="health"),
    path("", include("apps.authentication.urls")),
    path("", include("apps.accounts.urls")),
    path("", include("apps.courses.urls")),
    path("", include("apps.batches.urls")),
    path("", include("apps.enrollments.urls")),
    path("", include("apps.payments.urls")),
    path("", include("apps.certificates.urls")),
    path("", include("apps.referrals.urls")),
    path("", include("apps.reports.urls")),
    path("", include("apps.core.urls")),
]

# OpenAPI schema and interactive docs list every endpoint, so they are served
# only in development (DEBUG) and never on a public deployment.
if settings.DEBUG:
    api_v1 += [
        path("schema", SpectacularAPIView.as_view(), name="schema"),
        path("docs", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
        path("redoc", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),
    ]

urlpatterns = [
    path("api/v1/", include(api_v1)),
]

if settings.DEBUG:
    urlpatterns.append(path("django-admin/", admin.site.urls))
