"""Move issued units whose term ran out into the expired state.

    python manage.py sweep_expired

Without this command the stored state lags behind reality: a unit still
shows as issued long after it stopped working. Schedule it next to the
reminders, usually once an hour.

Idempotent by construction: a second run in a row finds zero units, because
the first one already moved everything it found.
"""

from typing import Any

from django.core.management.base import BaseCommand, CommandParser

from inventory import services


class Command(BaseCommand):
    help = "Move expired issued units into the expired state"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--dry-run", action="store_true", help="print the list, change nothing")

    def handle(self, *args: str, **options: Any) -> None:
        if options["dry_run"]:
            stale = list(services.stale_issued_units())
            for unit in stale:
                self.stdout.write(f"{unit.ref}: expired {unit.expires_at:%Y-%m-%d %H:%M}")
            self.stdout.write(self.style.WARNING(f"dry run, found {len(stale)}"))
            return

        moved = services.sweep_expired(actor="cron")
        self.stdout.write(self.style.SUCCESS(f"moved to expired: {moved}"))
