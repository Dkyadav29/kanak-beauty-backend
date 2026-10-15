from io import StringIO

import pytest
from django.core.management import CommandError, call_command

from apps.accounts.models import User

pytestmark = pytest.mark.django_db


def test_create_admin_creates_verified_admin():
    out = StringIO()
    call_command("create_admin", email="Boss@Kanak.test", name="Boss", password="Bootstrap@123", stdout=out)
    user = User.objects.get(email="boss@kanak.test")
    assert user.role == "admin"
    assert user.email_verified and user.is_staff and user.is_superuser and user.is_active
    assert user.check_password("Bootstrap@123")
    assert "created" in out.getvalue()


def test_create_admin_promotes_existing_user(student):
    out = StringIO()
    call_command("create_admin", email=student.email, name=student.name, stdout=out)
    student.refresh_from_db()
    assert student.role == "admin" and student.is_staff
    assert student.check_password("Student@123")  # unchanged without --password
    assert "promoted" in out.getvalue()


def test_create_admin_rejects_weak_password():
    with pytest.raises(CommandError):
        call_command("create_admin", email="boss@kanak.test", name="Boss", password="weak")
    assert not User.objects.filter(email="boss@kanak.test").exists()


def test_create_admin_requires_email_and_password():
    with pytest.raises(CommandError):
        call_command("create_admin", name="Boss", password="Bootstrap@123", email=None)
    with pytest.raises(CommandError):
        call_command("create_admin", email="boss@kanak.test", name="Boss", password=None)
