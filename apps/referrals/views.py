"""Refer & Earn endpoints: public code validation, student summary, admin list/stats/config."""

from __future__ import annotations

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.generics import GenericAPIView, ListAPIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.services import log_audit
from apps.referrals import selectors, services
from apps.referrals.models import ReferralProgramConfig
from apps.referrals.serializers import (
    ReferralAdminRowSerializer,
    ReferralAdminStatsSerializer,
    ReferralCodeValidationSerializer,
    ReferralProgramConfigSerializer,
    ReferralProgramConfigUpdateSerializer,
    ReferralSummarySerializer,
)
from common.permissions import HasPermission, IsStudent
from common.throttles import PublicLookupThrottle
from common.utils import query_param

TAG = "referrals"


class ValidateReferralCodeView(APIView):
    """Public: reveals only whether the code is valid, the referrer's first name and the welcome discount."""

    authentication_classes: list = []
    permission_classes = [AllowAny]
    throttle_classes = [PublicLookupThrottle]

    @extend_schema(tags=[TAG], summary="Validate a referral code", responses=ReferralCodeValidationSerializer)
    def get(self, request, code: str):
        cfg = ReferralProgramConfig.get()
        referrer = services.find_referrer_by_code(code) if cfg.enabled else None
        data = {
            "valid": referrer is not None,
            "referrer_first_name": referrer.name.split(" ")[0] if referrer is not None else None,
            "referee_discount_amount": cfg.referee_discount_amount if referrer is not None else 0,
            "currency": cfg.currency,
        }
        return Response(ReferralCodeValidationSerializer(data).data)


class StudentReferralsView(APIView):
    permission_classes = [IsStudent, HasPermission]
    required_permission = "referrals:read:self"

    @extend_schema(tags=[TAG], summary="My referrals and wallet", responses=ReferralSummarySerializer)
    def get(self, request):
        summary = selectors.student_summary(request.user, request)
        return Response(ReferralSummarySerializer(summary, context={"mask_emails": True}).data)


# --- Admin -------------------------------------------------------------------------------------


class AdminReferralListView(ListAPIView):
    permission_classes = [HasPermission]
    required_permission = "referrals:manage"
    serializer_class = ReferralAdminRowSerializer

    def get_queryset(self):
        return selectors.filter_admin_referrals(
            selectors.referrals_queryset(),
            search=query_param(self.request, "search"),
            status=query_param(self.request, "status"),
        )

    @extend_schema(
        tags=[TAG],
        summary="List referrals (admin)",
        parameters=[
            OpenApiParameter("search", str, description="Referrer/referee name or email, or referral code"),
            OpenApiParameter("status", str, description="signed_up|rewarded|cancelled|all"),
        ],
    )
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)


class AdminReferralStatsView(APIView):
    permission_classes = [HasPermission]
    required_permission = "referrals:manage"

    @extend_schema(tags=[TAG], summary="Referral programme statistics (admin)", responses=ReferralAdminStatsSerializer)
    def get(self, request):
        return Response(ReferralAdminStatsSerializer(selectors.admin_stats()).data)


class AdminReferralConfigView(GenericAPIView):
    permission_classes = [HasPermission]
    required_permission = "referrals:manage"
    serializer_class = ReferralProgramConfigUpdateSerializer

    @extend_schema(
        tags=[TAG], summary="Referral programme configuration (admin)", responses=ReferralProgramConfigSerializer
    )
    def get(self, request):
        return Response(ReferralProgramConfigSerializer(ReferralProgramConfig.get()).data)

    @extend_schema(
        tags=[TAG], summary="Update referral programme configuration (admin)", responses=ReferralProgramConfigSerializer
    )
    def put(self, request):
        body = self.get_serializer(data=request.data)
        body.is_valid(raise_exception=True)
        data = body.validated_data
        cfg = ReferralProgramConfig.get()
        previous = {
            "enabled": cfg.enabled,
            "reward": cfg.referrer_reward_amount,
            "discount": cfg.referee_discount_amount,
            "max_credit": cfg.max_credit_per_order,
        }
        cfg.enabled = data.get("enabled", True)
        cfg.referrer_reward_amount = data["referrer_reward_amount"]
        cfg.referee_discount_amount = data["referee_discount_amount"]
        cfg.max_credit_per_order = data["max_credit_per_order"]
        cfg.save()
        log_audit(
            actor=request.user,
            action="referrals.config",
            entity_type="referral_program",
            entity_id="default",
            details=(
                f"Program {'enabled' if cfg.enabled else 'disabled'}; "
                f"reward {previous['reward']:.0f} -> {cfg.referrer_reward_amount}; "
                f"discount {previous['discount']:.0f} -> {cfg.referee_discount_amount}; "
                f"max credit/order {previous['max_credit']:.0f} -> {cfg.max_credit_per_order}."
            ),
            request=request,
        )
        cfg.refresh_from_db()
        return Response(ReferralProgramConfigSerializer(cfg).data)
