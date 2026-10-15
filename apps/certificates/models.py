"""Certificates: issued/revoked certificates and a log of public verification lookups."""

from django.conf import settings
from django.db import models

from common.models import PrefixedIDModel


class Certificate(PrefixedIDModel):
    """
    A certificate is issued only by ``apps.certificates.services.issue_certificate``
    after server-side eligibility checks. Revocation keeps the row (status=revoked).
    At most one *issued* certificate per (student, course): MariaDB has no partial
    unique indexes, so the service enforces it under ``select_for_update``.
    """

    ID_PREFIX = "cert"

    STATUS_CHOICES = [("issued", "Issued"), ("revoked", "Revoked")]

    certificate_number = models.CharField(max_length=32, unique=True)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="certificates")
    student_name = models.CharField(max_length=120)  # snapshot at issuance
    course = models.ForeignKey("courses.Course", on_delete=models.PROTECT, related_name="certificates")
    course_title = models.CharField(max_length=160)  # snapshot at issuance
    batch = models.ForeignKey(
        "batches.Batch", null=True, blank=True, on_delete=models.SET_NULL, related_name="certificates"
    )
    completion_date = models.DateTimeField()
    issued_at = models.DateTimeField(auto_now_add=True, db_index=True)
    issued_by = models.CharField(max_length=120, default="Kanak Beauty Service")
    issued_by_user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default="issued", db_index=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_reason = models.CharField(max_length=240, blank=True, default="")
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reissued_from = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="reissues")

    class Meta:
        db_table = "certificates_certificate"
        ordering = ["-issued_at"]
        indexes = [models.Index(fields=["student", "course", "status"])]

    def __str__(self) -> str:
        return self.certificate_number

    @property
    def verification_path(self) -> str:
        return f"/verify/{self.certificate_number}"


class CertificateVerificationLog(models.Model):
    """Public verification lookups (for abuse monitoring)."""

    id = models.BigAutoField(primary_key=True)
    certificate_number = models.CharField(max_length=32, db_index=True)
    result = models.CharField(max_length=16)  # issued | revoked | not_found
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    checked_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "certificates_verification_log"
