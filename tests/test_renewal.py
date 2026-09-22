"""Продовження строку дії.

Головний тест файлу: ``test_renewal_does_not_eat_paid_days``. Це найчастіша
помилка в такій логіці, і саме через неї клієнти пишуть у підтримку.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from inventory import services
from inventory.models import Renewal
from inventory.states import UnitState


@pytest.mark.django_db
def test_renewal_does_not_eat_paid_days(make_unit):
    """Продовження додається до чинного строку, а не до «зараз».

    Клієнт продовжує за 10 днів до кінця на 365 днів. Правильний
    результат: 375 днів від сьогодні. Наївна реалізація ``now + 365``
    мовчки з'їдає 10 оплачених днів.
    """
    unit = make_unit("U-RENEW", state=UnitState.ISSUED, expires_in_days=10)
    before = unit.expires_at

    services.renew_unit(unit_ref="U-RENEW", period_days=365, price_cents=20000)

    unit.refresh_from_db()
    gained = unit.expires_at - before
    assert gained == timedelta(days=365)

    # І дата справді далеко попереду: 10 днів, що лишались, плюс 365.
    assert unit.expires_at > timezone.now() + timedelta(days=374)


@pytest.mark.django_db
def test_renewal_of_expired_counts_from_now(make_unit):
    """Якщо строк уже вийшов, відлік іде від «зараз», а не від минулого.

    Інакше продовження прострочки на 30 днів дало б дату, яка теж у
    минулому, і одиниця лишилась би непрацездатною після оплати.
    """
    unit = make_unit("U-STALE", state=UnitState.EXPIRED, expires_in_days=-40)

    before = timezone.now()
    services.renew_unit(unit_ref="U-STALE", period_days=30, price_cents=20000)
    after = timezone.now()

    unit.refresh_from_db()
    # Точна межа замість порівняння з "зараз": відлік стартував десь між
    # before і after, тому нова дата лежить рівно в цьому коридорі.
    # Перевірка з .days тут плавала б на межі мілісекунди.
    assert before + timedelta(days=30) <= unit.expires_at <= after + timedelta(days=30)


@pytest.mark.django_db
def test_renewal_returns_expired_unit_to_issued(make_unit):
    unit = make_unit("U-BACK", state=UnitState.EXPIRED, expires_in_days=-1)
    services.renew_unit(unit_ref="U-BACK", period_days=30, price_cents=100)
    unit.refresh_from_db()
    assert unit.state == UnitState.ISSUED


@pytest.mark.django_db
def test_renewal_records_both_dates(make_unit):
    """Історія має пояснювати, звідки взялась нова дата."""
    unit = make_unit("U-HIST", state=UnitState.ISSUED, expires_in_days=5)
    previous = unit.expires_at

    services.renew_unit(unit_ref="U-HIST", period_days=30, price_cents=500)

    renewal = Renewal.objects.get(unit=unit)
    assert renewal.previous_expires_at == previous
    assert renewal.new_expires_at == previous + timedelta(days=30)


@pytest.mark.django_db
def test_revoked_unit_cannot_be_renewed(make_unit):
    make_unit("U-DEAD", state=UnitState.REVOKED)
    with pytest.raises(services.IllegalTransition):
        services.renew_unit(unit_ref="U-DEAD", period_days=30, price_cents=500)


@pytest.mark.django_db
def test_first_renewal_of_unit_without_expiry(make_unit):
    """Одиниця без строку: відлік від «зараз», падати не має."""
    from inventory.models import Unit

    make_unit("U-FRESH", state=UnitState.ISSUED, expires_in_days=None)

    before = timezone.now()
    services.renew_unit(unit_ref="U-FRESH", period_days=365, price_cents=9900)
    after = timezone.now()

    expires_at = Unit.objects.get(ref="U-FRESH").expires_at
    assert before + timedelta(days=365) <= expires_at <= after + timedelta(days=365)
