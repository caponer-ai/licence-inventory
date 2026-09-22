"""Demo data, so the project can be looked at within a minute.

    python manage.py migrate
    python manage.py seed_demo
    python manage.py runserver

Also creates a `demo` user and prints its token, so the first request to
the API does not hit a 401.
"""

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from rest_framework.authtoken.models import Token

from inventory import services
from inventory.models import Client, Unit
from inventory.states import Tier, UnitState


class Command(BaseCommand):
    help = "Fill the database with demonstration data"

    @transaction.atomic
    def handle(self, *args: str, **options: Any) -> None:
        # This command creates a staff user with a password printed in the
        # README. On a real instance that is a back door, and the README
        # tells people to run the command immediately after migrate.
        if settings.IS_PRODUCTION:
            raise CommandError("seed_demo creates a demo staff user and never runs in production")

        if Unit.objects.exists():
            self.stdout.write(self.style.WARNING("database is not empty, skipping demo data"))
            return

        # Without a user and a token the README would promise a one-minute
        # start while the very first curl returned 401.
        demo, created = User.objects.get_or_create(username="demo", defaults={"is_staff": True})
        if created:
            demo.set_password("demo")
            demo.save()
        token, _ = Token.objects.get_or_create(user=demo)

        acme = Client.objects.create(name="Acme Ltd", contact="@acme")
        beta = Client.objects.create(name="Beta Studio", contact="beta@example.com")

        now = timezone.now()
        for i in range(1, 7):
            Unit.objects.create(
                ref=f"UNIT-{i:03d}",
                tier=Tier.INDIVIDUAL if i % 2 else Tier.COMPANY,
                cost_cents=9900 if i % 2 else 29900,
                acquired_at=now - timedelta(days=30 * i),
            )

        # Issued a while ago, expires in five days: will show up in reminders.
        issue = services.issue_unit(unit_ref="UNIT-001", client_id=acme.id, price_cents=35000, actor="demo")
        issue.unit.expires_at = now + timedelta(days=5)
        issue.unit.save(update_fields=["expires_at"])

        # Issued yesterday: warranty still open, and there is a claim on it.
        second = services.issue_unit(unit_ref="UNIT-002", client_id=beta.id, price_cents=65000, actor="demo")
        services.open_claim(issue_id=second.id, reason="stopped responding", actor="demo")

        # Already past its term: shows what sweep_expired does.
        third = services.issue_unit(unit_ref="UNIT-003", client_id=acme.id, price_cents=35000, actor="demo")
        third.unit.expires_at = now - timedelta(days=2)
        third.unit.save(update_fields=["expires_at"])

        moved = services.sweep_expired(actor="demo")

        self.stdout.write(
            self.style.SUCCESS(
                f"clients 2, units {Unit.objects.count()}, "
                f"issues 3, expired swept {moved}, "
                f"available {Unit.objects.filter(state=UnitState.AVAILABLE).count()}"
            )
        )
        self.stdout.write("")
        self.stdout.write(f"user demo / demo, token: {token.key}")
        self.stdout.write("try it right away:")
        self.stdout.write(f'  curl -H "Authorization: Token {token.key}" http://localhost:8000/api/units/')
