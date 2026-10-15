"""
Delete ALL academy data (demo/mock or real) from the configured database.

The schema (tables, migrations) is kept; only rows are removed. Use this to
clear the seed_demo dataset before entering real data, or to reset a UAT box.

    python manage.py purge_data --dry-run          # show what would be deleted
    python manage.py purge_data --yes              # delete everything, incl. users
    python manage.py purge_data --yes --keep-admins  # delete everything except admin users

Afterwards you can recreate an admin with `python manage.py create_admin`.
"""

from django.contrib.admin.models import LogEntry
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from rest_framework_simplejwt.token_blacklist.models import BlacklistedToken, OutstandingToken

from apps.accounts.models import LoginAttempt, OneTimeCode, PasswordResetToken, User
from apps.batches.models import AttendanceRecord, Batch, BatchEnrollment, BatchHistoryEvent, LiveSession, Recording
from apps.certificates.models import Certificate, CertificateVerificationLog
from apps.core.models import AuditLog
from apps.courses.models import Course, CourseModule, LearningMaterial, Lesson, Trainer
from apps.enrollments.models import CourseEnrollment, LessonProgress
from apps.payments.models import PaymentTransaction, WebhookEvent
from apps.referrals.models import Referral, ReferralProgramConfig, ReferralReward
from common.permissions import ROLE_ADMIN

# Child rows first so FK constraints never block the delete.
MODELS_IN_DELETE_ORDER = (
    LogEntry,
    AuditLog,
    CertificateVerificationLog,
    Certificate,
    ReferralReward,
    Referral,
    AttendanceRecord,
    Recording,
    LiveSession,
    BatchHistoryEvent,
    BatchEnrollment,
    LessonProgress,
    CourseEnrollment,
    WebhookEvent,
    PaymentTransaction,
    Batch,
    LearningMaterial,
    Lesson,
    CourseModule,
    Course,
    Trainer,
    ReferralProgramConfig,
    LoginAttempt,
    OneTimeCode,
    PasswordResetToken,
    BlacklistedToken,
    OutstandingToken,
)


class Command(BaseCommand):
    help = "Delete all academy data rows (keeps schema). Use --dry-run to preview, --yes to confirm."

    def add_arguments(self, parser):
        parser.add_argument("--yes", action="store_true", help="Actually delete. Without it, nothing is removed.")
        parser.add_argument("--dry-run", action="store_true", help="Only print the row counts that would be deleted.")
        parser.add_argument("--keep-admins", action="store_true", help="Keep users with the admin role (and their sessions).")

    def handle(self, *args, **options):
        keep_admins = options["keep_admins"]
        users_qs = User.objects.exclude(role=ROLE_ADMIN) if keep_admins else User.objects.all()

        plan: list[tuple[str, int]] = [(m._meta.label, m.objects.count()) for m in MODELS_IN_DELETE_ORDER]
        plan.append((f"{User._meta.label}{' (non-admin)' if keep_admins else ''}", users_qs.count()))
        total = sum(n for _, n in plan)

        self.stdout.write(f"Database: {self._db_label()}")
        for label, n in plan:
            if n:
                self.stdout.write(f"  {label:<45} {n:>7}")
        self.stdout.write(f"  {'TOTAL rows':<45} {total:>7}")

        if options["dry_run"]:
            self.stdout.write(self.style.WARNING("Dry run: nothing deleted."))
            return
        if not options["yes"]:
            raise CommandError("Refusing to delete without --yes (use --dry-run to preview).")
        if total == 0:
            self.stdout.write(self.style.SUCCESS("Nothing to delete."))
            return

        with transaction.atomic():
            for model in MODELS_IN_DELETE_ORDER:
                if keep_admins and model in (BlacklistedToken, OutstandingToken):
                    # Keep admin sessions alive; drop everyone else's tokens.
                    model.objects.exclude(user__role=ROLE_ADMIN).delete() if model is OutstandingToken else model.objects.exclude(
                        token__user__role=ROLE_ADMIN
                    ).delete()
                    continue
                model.objects.all().delete()
            users_qs.delete()

        remaining_users = User.objects.count()
        self.stdout.write(self.style.SUCCESS(f"Deleted {total} rows. Users remaining: {remaining_users}."))
        if remaining_users == 0:
            self.stdout.write("No users left. Create one with: python manage.py create_admin --email ... --password ...")

    @staticmethod
    def _db_label() -> str:
        from django.conf import settings

        db = settings.DATABASES["default"]
        return f"{db.get('ENGINE', '').rsplit('.', 1)[-1]} {db.get('NAME')} @ {db.get('HOST') or 'local'}"
