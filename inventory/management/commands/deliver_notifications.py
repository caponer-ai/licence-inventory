"""Deliver the reminders the service queued.

    python manage.py deliver_notifications

Separate from send_renewal_reminders on purpose: deciding to notify is a
database transaction, talking to a provider is not. Run it from cron a few
minutes behind the reminder job, or continuously.

Safe to run concurrently with itself: each notification is locked while it
is being delivered and skipped if another worker already took it.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from inventory import delivery


class Command(BaseCommand):
    help = "Deliver queued reminder notifications"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--limit", type=int, default=100, help="how many to attempt")

    def handle(self, *args: str, **options: Any) -> None:
        result = delivery.deliver_pending(limit=options["limit"])
        self.stdout.write(
            self.style.SUCCESS(
                f"sent {result['sent']}, retryable failures {result['failed']}, "
                f"given up {result['gave_up']}"
            )
        )
