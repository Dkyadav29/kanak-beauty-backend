"""URL patterns for the payments app. Paths have no trailing slash and are mounted under /api/v1/."""

from django.conf import settings
from django.urls import path

from apps.payments import views

urlpatterns = [
    path("payments/orders", views.CreateOrderView.as_view(), name="payments-create-order"),
    path("payments/webhook", views.WebhookView.as_view(), name="payments-webhook"),
    path("student/payments", views.StudentPaymentListView.as_view(), name="student-payments"),
    path("student/payments/<str:payment_id>", views.StudentPaymentDetailView.as_view(), name="student-payment"),
    path("admin/payments", views.AdminPaymentListView.as_view(), name="admin-payments"),
    path(
        "admin/payments/<str:payment_id>/reconcile",
        views.AdminPaymentReconcileView.as_view(),
        name="admin-payment-reconcile",
    ),
    path("admin/payments/<str:payment_id>/refund", views.AdminPaymentRefundView.as_view(), name="admin-payment-refund"),
]

# The checkout simulator exists only for the sandbox provider; production gateways settle via the webhook alone.
if settings.PAYMENT_GATEWAY == "sandbox":
    urlpatterns += [
        path("payments/sandbox/complete", views.SandboxCompleteView.as_view(), name="payments-sandbox-complete"),
    ]
