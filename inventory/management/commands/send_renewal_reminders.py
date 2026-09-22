"""Нагадування про продовження.

Запускається з cron або systemd-таймера:

    python manage.py send_renewal_reminders --days 14

Команда ідемпотентна: на один строк дії одне нагадування, скільки б разів
її не запустили. Тому безпечно ставити кожну годину, і перезапуск cron
після збою нічого не задублює.
"""

from django.core.management.base import BaseCommand, CommandParser

from inventory import services


class Command(BaseCommand):
    help = "Надіслати нагадування про продовження одиниць, у яких спливає строк"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument("--days", type=int, default=14, help="горизонт у днях")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="показати список і нічого не позначати",
        )

    def handle(self, *args: object, **options: object) -> None:
        days = options["days"]

        if options["dry_run"]:
            units = list(services.expiring_units(days))
            for unit in units:
                self.stdout.write(f"{unit.ref}: строк {unit.expires_at:%Y-%m-%d %H:%M}")
            self.stdout.write(
                self.style.WARNING(f"пробний запуск, знайдено {len(units)}")
            )
            return

        sent = services.send_renewal_reminders(days=days)
        for unit in sent:
            self.stdout.write(f"нагадано: {unit.ref} до {unit.expires_at:%Y-%m-%d}")
        self.stdout.write(self.style.SUCCESS(f"надіслано нагадувань: {len(sent)}"))
