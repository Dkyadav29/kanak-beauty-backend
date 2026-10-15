import pytest

from apps.certificates.models import CertificateVerificationLog

pytestmark = pytest.mark.django_db

VERIFY = "/api/v1/certificates/verify/{}"


def test_verify_valid_certificate_is_public(api, certificate):
    res = api.get(VERIFY.format(certificate.certificate_number))
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is True
    assert body["status"] == "issued"
    assert body["certificate"]["certificateNumber"] == certificate.certificate_number
    assert body["certificate"]["studentName"] == "Priya Sharma"
    assert body["certificate"]["courseTitle"] == certificate.course_title
    assert body["certificate"]["issuedBy"] == "Kanak Beauty Service"
    assert set(body["certificate"]) == {
        "certificateNumber",
        "studentName",
        "courseTitle",
        "completionDate",
        "issuedAt",
        "issuedBy",
        "status",
    }
    assert body["checkedAt"]
    log = CertificateVerificationLog.objects.get()
    assert log.certificate_number == certificate.certificate_number
    assert log.result == "issued"
    assert log.ip_address == "127.0.0.1"


def test_verify_is_case_insensitive(api, certificate):
    res = api.get(VERIFY.format(certificate.certificate_number.lower()))
    assert res.status_code == 200
    assert res.json()["valid"] is True


def test_verify_revoked_certificate_is_not_valid(api, revoked_certificate):
    res = api.get(VERIFY.format(revoked_certificate.certificate_number))
    assert res.status_code == 200
    body = res.json()
    assert body["valid"] is False
    assert body["status"] == "revoked"
    assert body["certificate"]["status"] == "revoked"
    assert CertificateVerificationLog.objects.get().result == "revoked"


def test_verify_unknown_number(api):
    res = api.get(VERIFY.format("KBA-2020-99999"))
    assert res.status_code == 200
    assert res.json() == {
        "valid": False,
        "status": "not_found",
        "certificate": None,
        "checkedAt": res.json()["checkedAt"],
    }
    log = CertificateVerificationLog.objects.get()
    assert log.result == "not_found"
    assert log.certificate_number == "KBA-2020-99999"
