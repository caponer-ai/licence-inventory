"""Renewal reminders.

Run from cron or a systemd timer:

    python manage.py send_renewal_reminders --days 14

The command is idempotent: one reminder per expiry date, no matter how many
times it runs. It is therefore safe to schedule hourly, and restarting cron
after a failure never duplicates anything.

It queues rather than sends. Deciding who needs a reminder belongs in a
database transaction; calling a provider does not, because a network round
trip inside a transaction holds row locks for its whole duration. Delivery
lives in deliver_notifications.
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

        queued = services.send_renewal_reminders(days=days)
        for unit in queued:
            self.stdout.write(f"queued: {unit.ref} expiring {unit.expires_at:%Y-%m-%d}")
        # Queued, not sent. This command decides who needs a reminder;
        # deliver_notifications is what talks to a provider. Saying "sent"
        # here was the original lie that the notification states replaced.
        self.stdout.write(
            self.style.SUCCESS(
                f"reminders queued: {len(queued)} (run deliver_notifications to send)"
            )
        )
