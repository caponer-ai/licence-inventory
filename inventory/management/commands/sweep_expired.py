"""Перевести видані одиниці з вичерпаним строком у стан «строк вийшов».

    python manage.py sweep_expired

Без цієї команди стан у базі відстає від реальності: одиниця показується
виданою ще довго після того, як перестала працювати. Ставиться в cron
поруч із нагадуваннями, зазвичай раз на годину.

Ідемпотентна за побудовою: другий запуск поспіль знайде нуль одиниць,
бо перший уже перевів усі знайдені.
"""

from django.core.management.base import BaseCommand

from inventory import services


class Command(BaseCommand):
    help = "Перевести прострочені видані одиниці у стан expired"

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="показати список і нічого не міняти")

    def handle(self, *args, **options):
        if options["dry_run"]:
            stale = list(services.stale_issued_units())
            for unit in stale:
                self.stdout.write(f"{unit.ref}: строк вийшов {unit.expires_at:%Y-%m-%d %H:%M}")
            self.stdout.write(self.style.WARNING(f"пробний запуск, знайдено {len(stale)}"))
            return

        moved = services.sweep_expired(actor="cron")
        self.stdout.write(self.style.SUCCESS(f"переведено в expired: {moved}"))
