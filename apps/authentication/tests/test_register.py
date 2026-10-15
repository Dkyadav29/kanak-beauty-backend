import pytest

from apps.accounts.models import OneTimeCode, User
from apps.referrals.models import Referral

from .conftest import REGISTER_URL

pytestmark = pytest.mark.django_db


def test_register_success_creates_unverified_student_and_sends_otp(api, register_payload, mailoutbox, otp_from_mail):
    res = api.post(REGISTER_URL, register_payload, format="json")
    assert res.status_code == 201, res.content
    body = res.json()
    assert body["email"] == "riya@student.test"
    assert body["verificationRequired"] is True
    user = User.objects.get(pk=body["userId"])
    assert user.role == "student"
    assert user.email_verified is False
    assert user.referral_code
    assert user.check_password("Secure@123")
    # Response never leaks the password or the code.
    assert "Secure@123" not in res.content.decode()
    assert "password" not in body
    # One active OTP stored as a hash, raw code only in the email.
    code = otp_from_mail(user.email)
    row = OneTimeCode.objects.get(user=user, consumed_at__isnull=True)
    assert row.code_hash != code and len(row.code_hash) == 64
    assert len(mailoutbox) == 1 and code not in res.content.decode()


def test_register_normalises_email_case(api, register_payload):
    register_payload["email"] = "Riya@Student.TEST"
    res = api.post(REGISTER_URL, register_payload, format="json")
    assert res.status_code == 201
    assert res.json()["email"] == "riya@student.test"


def test_register_duplicate_email_409(api, register_payload, student):
    register_payload["email"] = student.email.upper()
    res = api.post(REGISTER_URL, register_payload, format="json")
    assert res.status_code == 409
    body = res.json()
    assert body["code"] == "EMAIL_TAKEN"
    assert "email" in body["details"]


@pytest.mark.parametrize("weak", ["short1", "password", "12345678", "abcdefgh"])
def test_register_weak_password_400(api, register_payload, weak):
    register_payload["password"] = weak
    res = api.post(REGISTER_URL, register_payload, format="json")
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "VALIDATION"
    assert "password" in body["details"]
    assert not User.objects.filter(email="riya@student.test").exists()


def test_register_invalid_mobile_400(api, register_payload):
    register_payload["mobile"] = "abc"
    res = api.post(REGISTER_URL, register_payload, format="json")
    assert res.status_code == 400
    assert "mobile" in res.json()["details"]


def test_register_role_in_body_is_ignored(api, register_payload):
    register_payload["role"] = "admin"
    res = api.post(REGISTER_URL, register_payload, format="json")
    assert res.status_code == 201
    user = User.objects.get(pk=res.json()["userId"])
    assert user.role == "student"
    assert user.is_staff is False and user.is_superuser is False


def test_register_invalid_referral_code_400_and_no_user_created(api, register_payload, referral_config):
    register_payload["referralCode"] = "NOPE-0000"
    res = api.post(REGISTER_URL, register_payload, format="json")
    assert res.status_code == 400
    body = res.json()
    assert body["code"] == "INVALID_REFERRAL_CODE"
    assert "referralCode" in body["details"]
    assert not User.objects.filter(email="riya@student.test").exists()


def test_register_referral_disabled_programme_400(api, register_payload, referral_config, student):
    referral_config.enabled = False
    referral_config.save()
    register_payload["referralCode"] = student.referral_code
    res = api.post(REGISTER_URL, register_payload, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_REFERRAL_CODE"


def test_register_valid_referral_code_links_referrer(api, register_payload, referral_config, student):
    register_payload["referralCode"] = student.referral_code.lower()  # case-insensitive
    res = api.post(REGISTER_URL, register_payload, format="json")
    assert res.status_code == 201, res.content
    user = User.objects.get(pk=res.json()["userId"])
    assert user.referred_by_id == student.pk
    referral = Referral.objects.get(referee=user)
    assert referral.referrer_id == student.pk
    assert referral.status == "signed_up"
    assert referral.referral_code == student.referral_code


def test_register_referral_code_of_inactive_user_rejected(api, register_payload, referral_config, student):
    student.is_active = False
    student.save(update_fields=["is_active"])
    register_payload["referralCode"] = student.referral_code
    res = api.post(REGISTER_URL, register_payload, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_REFERRAL_CODE"


def test_register_self_referral_by_mobile_rejected(api, register_payload, referral_config, student):
    register_payload["mobile"] = student.mobile
    register_payload["referralCode"] = student.referral_code
    res = api.post(REGISTER_URL, register_payload, format="json")
    assert res.status_code == 400
    assert res.json()["code"] == "INVALID_REFERRAL_CODE"


def test_register_blank_referral_code_is_ignored(api, register_payload, referral_config):
    register_payload["referralCode"] = ""
    res = api.post(REGISTER_URL, register_payload, format="json")
    assert res.status_code == 201
    assert not Referral.objects.exists()
