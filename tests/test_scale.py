"""Кількість запитів до бази не має рости разом з даними.

Третій раунд критики: наївні нагадування робили близько п'яти запитів на
одиницю. На десяти одиницях це 51 запит і ніхто не помічає. На десяти
тисячах ліцензій це вже десятки тисяч звернень за один запуск cron, і
помічає це прод, а не тест.

Тому тут перевіряється не швидкість (вона залежить від машини), а форма
залежності: скільки запитів на 5 одиниць і скільки на 40. Якщо числа
збігаються, залежності від обсягу немає.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from inventory import services
from inventory.models import Event, ReminderLog, Unit
from inventory.states import UnitState

#: Точні числа, а не «менше ніж». Якщо десь з'явиться зайвий запит до
#: бази, тест має впасти, а не мовчки пропустити регресію.
EXPECTED_REMIND = 4
EXPECTED_REMIND_REPEAT = 2
EXPECTED_SWEEP = 3

#: Керування транзакцією не рахуємо. Django обгортає кожен тест у
#: транзакцію, і кількість SAVEPOINT/RELEASE залежить від бекенда, тому
#: точні числа з ними були б правильні на SQLite і хибні на Postgres.
#: CI ганяє обидві бази, тож тест має бути незалежним від бекенда.
TRANSACTION_NOISE = ("SAVEPOINT", "RELEASE", "ROLLBACK", "BEGIN", "COMMIT")


def real_queries(captured) -> list[str]:
    return [
        q["sql"]
        for q in captured
        if not q["sql"].upper().lstrip().startswith(TRANSACTION_NOISE)
    ]


class count_queries(CaptureQueriesContext):
    """Лічильник запитів без транзакційного шуму."""

    def __init__(self):
        super().__init__(connection)

    @property
    def real(self) -> list[str]:
        return real_queries(self.captured_queries)


def make_many(prefix: str, count: int, *, days: int, state=UnitState.ISSUED):
    now = timezone.now()
    Unit.objects.bulk_create(
        [Unit(ref=f"{prefix}-{i}", state=state, expires_at=now + timedelta(days=days)) for i in range(count)]
    )


@pytest.mark.django_db
def test_reminders_query_count_does_not_grow_with_size():
    make_many("SM", 5, days=3)
    with count_queries() as small:
        services.send_renewal_reminders(days=14)

    Unit.objects.all().delete()
    ReminderLog.objects.all().delete()

    make_many("BIG", 40, days=3)
    with count_queries() as big:
        services.send_renewal_reminders(days=14)

    assert len(small.real) == EXPECTED_REMIND, small.real
    assert len(big.real) == EXPECTED_REMIND, big.real


@pytest.mark.django_db
def test_second_run_costs_almost_nothing():
    """Повторний запуск cron не має платити за вже надіслане."""
    make_many("AG", 20, days=3)
    services.send_renewal_reminders(days=14)

    with count_queries() as repeat:
        assert services.send_renewal_reminders(days=14) == []
    assert len(repeat.real) == EXPECTED_REMIND_REPEAT, repeat.real


@pytest.mark.django_db
def test_sweep_query_count_does_not_grow_with_size():
    make_many("SW", 30, days=-1)
    with count_queries() as swept:
        moved = services.sweep_expired()
    assert moved == 30
    assert len(swept.real) == EXPECTED_SWEEP, swept.real


@pytest.mark.django_db
def test_sweep_writes_one_event_per_unit():
    """Страховка для пакетного підмітання.

    ``sweep_expired`` єдине місце, де стан міняється повз ``move_state``.
    Інваріант «кожна зміна стану лишає слід» тримається цим тестом.
    """
    make_many("EV", 7, days=-1)

    moved = services.sweep_expired(actor="cron")

    events = Event.objects.filter(action="unit.state_changed", actor="cron")
    assert moved == 7
    assert events.count() == 7
    assert {e.payload["to"] for e in events} == {UnitState.EXPIRED}


@pytest.mark.django_db
def test_bulk_reminders_stay_idempotent_across_many_units():
    """Пакетна версія не має загубити головну властивість наївної."""
    make_many("ID", 25, days=5)

    first = services.send_renewal_reminders(days=14)
    second = services.send_renewal_reminders(days=14)

    assert len(first) == 25
    assert second == []
    assert ReminderLog.objects.count() == 25
