"""Демо-дані, щоб проєкт можна було подивитись за хвилину.

python manage.py migrate
python manage.py seed_demo
python manage.py send_renewal_reminders --days 14 --dry-run
python manage.py runserver
"""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from inventory import services
from inventory.models import Client, Unit
from inventory.states import Tier, UnitState


class Command(BaseCommand):
    help = "Заповнити базу демонстраційними даними"

    @transaction.atomic
    def handle(self, *args, **options):
        if Unit.objects.exists():
            self.stdout.write(self.style.WARNING("база не порожня, демо не заливаю"))
            return

        acme = Client.objects.create(name="ТОВ Акме", contact="@acme")
        beta = Client.objects.create(name="Beta Studio", contact="beta@example.com")

        now = timezone.now()
        for i in range(1, 7):
            Unit.objects.create(
                ref=f"UNIT-{i:03d}",
                tier=Tier.INDIVIDUAL if i % 2 else Tier.COMPANY,
                cost_cents=9900 if i % 2 else 29900,
                acquired_at=now - timedelta(days=30 * i),
            )

        # Видана давно, строк спливає за 5 днів: потрапить у нагадування.
        issue = services.issue_unit(
            unit_ref="UNIT-001", client_id=acme.id, price_cents=35000, actor="demo"
        )
        issue.unit.expires_at = now + timedelta(days=5)
        issue.unit.save(update_fields=["expires_at"])

        # Видана вчора: гарантія ще відкрита, є рекламація.
        second = services.issue_unit(
            unit_ref="UNIT-002", client_id=beta.id, price_cents=65000, actor="demo"
        )
        services.open_claim(
            issue_id=second.id, reason="перестав відповідати", actor="demo"
        )

        # Прострочена: побачимо, як її підмітає sweep_expired.
        third = services.issue_unit(
            unit_ref="UNIT-003", client_id=acme.id, price_cents=35000, actor="demo"
        )
        third.unit.expires_at = now - timedelta(days=2)
        third.unit.save(update_fields=["expires_at"])

        moved = services.sweep_expired(actor="demo")

        self.stdout.write(
            self.style.SUCCESS(
                f"клієнтів 2, одиниць {Unit.objects.count()}, "
                f"видач 3, прострочених підмічено {moved}, "
                f"вільних {Unit.objects.filter(state=UnitState.AVAILABLE).count()}"
            )
        )
