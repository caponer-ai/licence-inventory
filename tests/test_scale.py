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
from django.utils import timezone

from inventory import services
from inventory.models import Event, ReminderLog, Unit
from inventory.states import UnitState

#: Точні числа, а не «менше ніж». Якщо десь з'явиться зайвий запит,
#: тест має впасти, а не мовчки пропустити регресію.
#: У кожне входять SAVEPOINT і RELEASE від вкладеної транзакції тесту.
EXPECTED_REMIND = 6
EXPECTED_REMIND_REPEAT = 4
EXPECTED_SWEEP = 5


def make_many(prefix: str, count: int, *, days: int, state=UnitState.ISSUED):
    now = timezone.now()
    Unit.objects.bulk_create(
        [Unit(ref=f"{prefix}-{i}", state=state, expires_at=now + timedelta(days=days)) for i in range(count)]
    )


@pytest.mark.django_db
def test_reminders_query_count_does_not_grow_with_size(django_assert_num_queries):
    make_many("SM", 5, days=3)
    with django_assert_num_queries(EXPECTED_REMIND) as small:
        services.send_renewal_reminders(days=14)

    Unit.objects.all().delete()
    ReminderLog.objects.all().delete()

    make_many("BIG", 40, days=3)
    with django_assert_num_queries(EXPECTED_REMIND) as big:
        services.send_renewal_reminders(days=14)

    assert len(small.captured_queries) == len(big.captured_queries)


@pytest.mark.django_db
def test_second_run_costs_almost_nothing(django_assert_num_queries):
    """Повторний запуск cron не має платити за вже надіслане."""
    make_many("AG", 20, days=3)
    services.send_renewal_reminders(days=14)

    with django_assert_num_queries(EXPECTED_REMIND_REPEAT):
        assert services.send_renewal_reminders(days=14) == []


@pytest.mark.django_db
def test_sweep_query_count_does_not_grow_with_size(django_assert_num_queries):
    make_many("SW", 30, days=-1)
    with django_assert_num_queries(EXPECTED_SWEEP):
        moved = services.sweep_expired()
    assert moved == 30


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
