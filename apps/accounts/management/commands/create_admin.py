"""
Bootstrap the first admin account.

    python manage.py create_admin --email admin@example.com --name "Kanak Admin" --password '...'

Values may also come from the environment: ADMIN_EMAIL, ADMIN_NAME, ADMIN_PASSWORD, ADMIN_MOBILE.
If the account already exists it is promoted to admin (verified, active, staff) and the
password is updated only when one is supplied. The command is idempotent for deploy scripts.
"""

from __future__ import annotations

import os

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import User
from common.permissions import ROLE_ADMIN


class Command(BaseCommand):
    help = "Create (or promote) an admin user. Reads ADMIN_EMAIL/ADMIN_NAME/ADMIN_PASSWORD/ADMIN_MOBILE when flags are omitted."

    def add_arguments(self, parser):
        parser.add_argument("--email", default=os.environ.get("ADMIN_EMAIL"))
        parser.add_argument("--name", default=os.environ.get("ADMIN_NAME", "Kanak Admin"))
        parser.add_argument("--password", default=os.environ.get("ADMIN_PASSWORD"))
        parser.add_argument("--mobile", default=os.environ.get("ADMIN_MOBILE", "+91 90000 00000"))

    def handle(self, *args, **options):
        email = (options.get("email") or "").strip().lower()
        name = (options.get("name") or "").strip()
        password = options.get("password")
        mobile = (options.get("mobile") or "").strip()
        if not email:
            raise CommandError("An email is required (--email or ADMIN_EMAIL).")
        if not name:
            raise CommandError("A name is required (--name or ADMIN_NAME).")

        existing = User.objects.filter(email__iexact=email).first()
        if existing is None and not password:
            raise CommandError("A password is required to create a new admin (--password or ADMIN_PASSWORD).")

        if password:
            try:
                validate_password(password, user=existing or User(email=email, name=name))
            except ValidationError as exc:
                raise CommandError("Password rejected: " + " ".join(exc.messages))

        if existing is None:
            User.objects.create_superuser(email=email, password=password, name=name, mobile=mobile)
            self.stdout.write(self.style.SUCCESS(f"Admin account created for {email}."))
            return

        existing.role = ROLE_ADMIN
        existing.is_staff = True
        existing.is_superuser = True
        existing.is_active = True
        existing.email_verified = True
        fields = ["role", "is_staff", "is_superuser", "is_active", "email_verified", "updated_at"]
        if password:
            existing.set_password(password)
            fields.append("password")
        existing.save(update_fields=fields)
        self.stdout.write(self.style.SUCCESS(f"Existing account {email} promoted to admin."))
