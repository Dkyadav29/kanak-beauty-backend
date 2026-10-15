"""Payment endpoints: orders, gateway webhook, sandbox checkout, student history and admin management."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework import status
from rest_framework.generics import GenericAPIView, ListAPIView, RetrieveAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.payments import services
from apps.payments.models import PaymentTransaction
from apps.payments.serializers import (
    CreateOrderRequestSerializer,
    CreateOrderResponseSerializer,
    PaymentTransactionSerializer,
    ReconcileResponseSerializer,
    RefundRequestSerializer,
    SandboxCompleteSerializer,
    SandboxResultSerializer,
    WebhookAckSerializer,
    WebhookPayloadSerializer,
    order_response,
)
from common.permissions import HasPermission, IsStudent
from common.utils import query_param

TAG = "payments"


class CreateOrderView(GenericAPIView):
    permission_classes = [IsStudent]
    serializer_class = CreateOrderRequestSerializer

    @extend_schema(tags=[TAG], summary="Create a pending order", responses={201: CreateOrderResponseSerializer})
    def post(self, request):
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        payment = services.create_order(request.user, **body.validated_data)
        return Response(CreateOrderResponseSerializer(order_response(payment)).data, status=status.HTTP_201_CREATED)


class WebhookView(APIView):
    """Gateway -> backend. No user context; authenticity comes from the HMAC signature over the raw body."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes: list = []

    @extend_schema(
        tags=[TAG],
        summary="Payment gateway webhook",
        request=WebhookPayloadSerializer,
        responses=WebhookAckSerializer,
        parameters=[OpenApiParameter(services.SIGNATURE_HEADER, str, OpenApiParameter.HEADER, required=True)],
    )
    def post(self, request):
        raw_body = request.body  # read before DRF parses request.data
        result = services.handle_webhook(raw_body, request.headers.get(services.SIGNATURE_HEADER))
        return Response({"received": True, "idempotent": result.idempotent})


class SandboxCompleteView(GenericAPIView):
    """Sandbox only (registered when PAYMENT_GATEWAY == 'sandbox'): simulates the hosted checkout."""

    permission_classes = [IsStudent]
    serializer_class = SandboxCompleteSerializer

    @extend_schema(tags=[TAG], summary="Simulate the sandbox checkout outcome", responses=SandboxResultSerializer)
    def post(self, request):
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        payment = services.sandbox_complete(request.user, **body.validated_data)
        return Response({"status": payment.status})


# --- Student -----------------------------------------------------------------------------------


class StudentPaymentListView(ListAPIView):
    permission_classes = [IsStudent, HasPermission]
    required_permission = "payments:read:self"
    serializer_class = PaymentTransactionSerializer
    pagination_class = None

    def get_queryset(self):
        return (
            PaymentTransaction.objects.filter(student=self.request.user)
            .select_related("student", "course")
            .order_by("-created_at")
        )

    @extend_schema(tags=[TAG], summary="My payments")
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class StudentPaymentDetailView(RetrieveAPIView):
    permission_classes = [IsStudent, HasPermission]
    required_permission = "payments:read:self"
    serializer_class = PaymentTransactionSerializer
    lookup_url_kwarg = "payment_id"

    def get_queryset(self):
        # Ownership: another student's payment is a 404, never a 403.
        return PaymentTransaction.objects.filter(student=self.request.user).select_related("student", "course")

    @extend_schema(tags=[TAG], summary="My payment (polled by the checkout page)")
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


# --- Admin -------------------------------------------------------------------------------------


class AdminPaymentListView(ListAPIView):
    permission_classes = [HasPermission]
    required_permission = "payments:read"
    serializer_class = PaymentTransactionSerializer

    def get_queryset(self):
        return services.filter_admin_payments(
            services.admin_payments_queryset(),
            search=query_param(self.request, "search"),
            status=query_param(self.request, "status"),
            date_from=query_param(self.request, "from"),
            date_to=query_param(self.request, "to"),
        )

    @extend_schema(
        tags=[TAG],
        summary="List payments (admin)",
        parameters=[
            OpenApiParameter("search", str, description="Order id, student name, course title or gateway reference"),
            OpenApiParameter("status", str, description="pending|paid|failed|refunded|cancelled|all"),
            OpenApiParameter("from", str, description="ISO date/datetime (inclusive)"),
            OpenApiParameter("to", str, description="ISO date (inclusive)"),
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class AdminPaymentReconcileView(APIView):
    permission_classes = [HasPermission]
    required_permission = "payments:reconcile"

    @extend_schema(
        tags=[TAG], summary="Reconcile with the gateway (admin)", request=None, responses=ReconcileResponseSerializer
    )
    def post(self, request, payment_id: str):
        payment, gateway_status, changed = services.reconcile_payment(
            payment_id=payment_id, actor=request.user, request=request
        )
        data = {"payment": payment, "gateway_status": gateway_status, "changed": changed}
        return Response(ReconcileResponseSerializer(data).data)


class AdminPaymentRefundView(GenericAPIView):
    permission_classes = [HasPermission]
    required_permission = "payments:reconcile"
    serializer_class = RefundRequestSerializer

    @extend_schema(tags=[TAG], summary="Refund a paid transaction (admin)", responses=PaymentTransactionSerializer)
    def post(self, request, payment_id: str):
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        payment = services.refund_payment(
            payment_id=payment_id, reason=body.validated_data["reason"], actor=request.user, request=request
        )
        return Response(PaymentTransactionSerializer(payment).data)
