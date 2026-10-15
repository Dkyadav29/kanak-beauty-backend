"""Refer & Earn endpoints: public validation, student summary, admin list/stats/config."""

from __future__ import annotations

from decimal import Decimal

import pytest

from apps.core.models import AuditLog
from apps.referrals import services
from apps.referrals.models import Referral, ReferralReward

pytestmark = pytest.mark.django_db

VALIDATE = "/api/v1/referrals/validate"
STUDENT_URL = "/api/v1/student/referrals"
ADMIN_URL = "/api/v1/admin/referrals"


@pytest.fixture
def find_referrer(monkeypatch):
    """find_referrer_by_code is implemented by the accounts/auth agent; emulate its contract here."""
    from apps.accounts.models import User

    def _find(code: str):
        normalized = (code or "").strip().upper()
        return User.objects.filter(referral_code__iexact=normalized, is_active=True).first() if normalized else None

    monkeypatch.setattr(services, "find_referrer_by_code", _find)
    return _find


@pytest.fixture
def referral(student, other_student, referral_config):
    student.referred_by = other_student
    student.save()
    return Referral.objects.create(referrer=other_student, referee=student, referral_code=other_student.referral_code)


def test_validate_code(api, other_student, referral_config, find_referrer):
    res = api.get(f"{VALIDATE}/{other_student.referral_code.lower()}")
    assert res.status_code == 200
    assert res.json() == {
        "valid": True,
        "referrerFirstName": "Ananya",
        "refereeDiscountAmount": 300.0,
        "currency": "INR",
    }
    res = api.get(f"{VALIDATE}/NOPE-0000")
    assert res.json() == {"valid": False, "referrerFirstName": None, "refereeDiscountAmount": 0, "currency": "INR"}
    assert "email" not in res.content.decode().lower()


def test_validate_code_disabled_programme(api, other_student, referral_config, find_referrer):
    referral_config.enabled = False
    referral_config.save()
    res = api.get(f"{VALIDATE}/{other_student.referral_code}")
    assert res.json()["valid"] is False
    assert res.json()["refereeDiscountAmount"] == 0


def test_student_summary(student_client, student, other_student, recorded_course, referral_config):
    # `student` referred `other_student`, who converted.
    from apps.payments.models import PaymentTransaction

    payment = PaymentTransaction.objects.create(
        order_id="KBA-ORD-5001",
        student=other_student,
        course=recorded_course,
        amount=1,
        list_price=1,
        status="paid",
        checkout_token="tok-5001",
    )
    referral = Referral.objects.create(
        referrer=student,
        referee=other_student,
        referral_code=student.referral_code,
        status="rewarded",
        reward_amount=Decimal("500"),
        payment=payment,
    )
    ReferralReward.objects.create(
        referral=referral, user=student, payment=payment, amount=Decimal("500"), status="credited", note="Reward"
    )
    ReferralReward.objects.create(
        user=student, amount=Decimal("-200"), status="redeemed", note="Credit applied to order KBA-ORD-1."
    )
    ReferralReward.objects.create(
        user=student, amount=Decimal("50"), status="credited", note="Goodwill credit (no referral)"
    )

    res = student_client.get(STUDENT_URL, HTTP_X_PUBLIC_ORIGIN="https://kanak.example")
    assert res.status_code == 200, res.content
    body = res.json()
    assert body["program"] == {
        "enabled": True,
        "referrerRewardAmount": 500.0,
        "refereeDiscountAmount": 300.0,
        "currency": "INR",
        "maxCreditPerOrder": 2000.0,
    }
    assert body["referralCode"] == student.referral_code
    assert body["referralLink"] == f"https://kanak.example/register?ref={student.referral_code}"
    assert body["stats"] == {"signedUp": 1, "converted": 1, "totalEarned": 500.0, "walletBalance": 350.0}
    assert len(body["referrals"]) == 1
    row = body["referrals"][0]
    assert row["id"] == referral.id
    assert row["refereeName"] == other_student.name
    assert row["refereeEmail"] == "an•••@student.test"
    assert row["status"] == "rewarded"
    assert row["rewardAmount"] == 500.0
    assert row["paymentId"] == payment.id
    assert row["courseTitle"] == recorded_course.title
    assert set(row) == {
        "id",
        "referrerId",
        "referralCode",
        "refereeId",
        "refereeName",
        "refereeEmail",
        "status",
        "createdAt",
        "convertedAt",
        "rewardAmount",
        "paymentId",
        "courseTitle",
    }
    assert len(body["rewards"]) == 3
    assert set(body["rewards"][0]) == {
        "id",
        "referralId",
        "userId",
        "amount",
        "currency",
        "status",
        "note",
        "createdAt",
    }


def test_student_summary_uses_configured_origin_without_header(student_client, student, settings):
    settings.PUBLIC_SITE_URL = "https://academy.example/"
    body = student_client.get(STUDENT_URL).json()
    assert body["referralLink"] == f"https://academy.example/register?ref={student.referral_code}"
    assert body["referrals"] == [] and body["rewards"] == []


def test_student_summary_permissions(admin_client, api):
    assert admin_client.get(STUDENT_URL).status_code == 403
    assert api.get(STUDENT_URL).status_code == 401


# --- Admin -------------------------------------------------------------------------------------


def test_admin_list_filters_and_pagination(admin_client, referral, student, other_student, unverified_student):
    second = Referral.objects.create(
        referrer=other_student, referee=unverified_student, referral_code=other_student.referral_code, status="rewarded"
    )
    res = admin_client.get(ADMIN_URL)
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 2
    row = next(r for r in body["items"] if r["id"] == referral.id)
    assert row["referrerName"] == other_student.name
    assert row["referrerEmail"] == other_student.email
    assert row["refereeEmail"] == student.email  # unmasked for admins
    assert row["courseTitle"] is None

    def ids(**params):
        return {r["id"] for r in admin_client.get(ADMIN_URL, params).json()["items"]}

    assert ids(status="rewarded") == {second.id}
    assert ids(status="signed_up") == {referral.id}
    assert ids(search="zoya") == {second.id}
    assert ids(search="priya") == {referral.id}
    assert ids(search=other_student.referral_code.lower()) == {referral.id, second.id}
    page = admin_client.get(ADMIN_URL, {"page": 2, "pageSize": 1}).json()
    assert page["total"] == 2 and page["page"] == 2 and page["pageSize"] == 1 and len(page["items"]) == 1


def test_admin_stats(admin_client, referral, student, other_student, unverified_student, referral_config):
    Referral.objects.create(
        referrer=other_student, referee=unverified_student, referral_code=other_student.referral_code, status="rewarded"
    )
    ReferralReward.objects.create(
        referral=referral, user=other_student, amount=Decimal("500"), status="credited", note="r"
    )
    ReferralReward.objects.create(user=other_student, amount=Decimal("-200"), status="redeemed", note="x")
    ReferralReward.objects.create(user=student, amount=Decimal("100"), status="credited", note="goodwill")
    ReferralReward.objects.create(
        user=unverified_student, amount=Decimal("-50"), status="reversed", note="negative balance ignored"
    )
    res = admin_client.get(f"{ADMIN_URL}/stats")
    assert res.status_code == 200
    assert res.json() == {
        "totalReferrals": 2,
        "converted": 1,
        "conversionRate": 50,
        "rewardsCredited": 500.0,
        "rewardsRedeemed": 200.0,
        "outstandingBalance": 400.0,
        "currency": "INR",
    }


def test_admin_stats_empty(admin_client, referral_config):
    body = admin_client.get(f"{ADMIN_URL}/stats").json()
    assert body["totalReferrals"] == 0 and body["conversionRate"] == 0 and body["outstandingBalance"] == 0


def test_admin_config_get_and_put(admin_client, admin_user, referral_config):
    res = admin_client.get(f"{ADMIN_URL}/config")
    assert res.status_code == 200
    assert res.json() == {
        "enabled": True,
        "referrerRewardAmount": 500.0,
        "refereeDiscountAmount": 300.0,
        "currency": "INR",
        "maxCreditPerOrder": 2000.0,
    }
    res = admin_client.put(
        f"{ADMIN_URL}/config",
        {
            "enabled": False,
            "referrerRewardAmount": 750,
            "refereeDiscountAmount": 250,
            "maxCreditPerOrder": 1000,
            "currency": "USD",
        },
        format="json",
    )
    assert res.status_code == 200, res.content
    assert res.json() == {
        "enabled": False,
        "referrerRewardAmount": 750.0,
        "refereeDiscountAmount": 250.0,
        "currency": "INR",
        "maxCreditPerOrder": 1000.0,
    }
    referral_config.refresh_from_db()
    assert referral_config.enabled is False and referral_config.referrer_reward_amount == Decimal("750")
    log = AuditLog.objects.get(action="referrals.config")
    assert log.actor == admin_user
    assert log.entity_type == "referral_program" and log.entity_id == "default"
    assert "500 -> 750" in log.details and "disabled" in log.details


@pytest.mark.parametrize(
    "payload",
    [
        {"referrerRewardAmount": 100.5, "refereeDiscountAmount": 100, "maxCreditPerOrder": 100},
        {"referrerRewardAmount": -1, "refereeDiscountAmount": 100, "maxCreditPerOrder": 100},
        {"referrerRewardAmount": 100, "refereeDiscountAmount": 100001, "maxCreditPerOrder": 100},
        {"referrerRewardAmount": 100, "refereeDiscountAmount": 100},
    ],
)
def test_admin_config_validation(admin_client, referral_config, payload):
    res = admin_client.put(f"{ADMIN_URL}/config", payload, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "VALIDATION"
    assert AuditLog.objects.count() == 0


def test_admin_permissions(student_client, api, referral_config):
    for url in (ADMIN_URL, f"{ADMIN_URL}/stats", f"{ADMIN_URL}/config"):
        assert student_client.get(url).status_code == 403
        assert api.get(url).status_code == 401
    payload = {"referrerRewardAmount": 1, "refereeDiscountAmount": 1, "maxCreditPerOrder": 1}
    assert student_client.put(f"{ADMIN_URL}/config", payload, format="json").status_code == 403
    assert api.put(f"{ADMIN_URL}/config", payload, format="json").status_code == 401
