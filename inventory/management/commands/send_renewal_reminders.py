"""Renewal reminders.

Run from cron or a systemd timer:

    python manage.py send_renewal_reminders --days 14

The command is idempotent: one reminder per expiry date, no matter how many
times it runs. It is therefore safe to schedule hourly, and restarting cron
after a failure never duplicates a send.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from inventory import services


class Command(BaseCommand):
    help = "Send renewal reminders for units whose term is running out"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=14, help="horizon in days")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="print the list and mark nothing",
        )

    def handle(self, *args: str, **options: Any) -> None:
        days = options["days"]

        if options["dry_run"]:
            units = list(services.expiring_units(days))
            for unit in units:
                self.stdout.write(f"{unit.ref}: expires {unit.expires_at:%Y-%m-%d %H:%M}")
            self.stdout.write(self.style.WARNING(f"dry run, found {len(units)}"))
            return

        sent = services.send_renewal_reminders(days=days)
        for unit in sent:
            self.stdout.write(f"reminded: {unit.ref} until {unit.expires_at:%Y-%m-%d}")
        self.stdout.write(self.style.SUCCESS(f"reminders sent: {len(sent)}"))
