"""Нагадування про продовження і підмітання прострочених."""

from io import StringIO

import pytest
from django.core.management import call_command

from inventory import services
from inventory.models import Event, ReminderLog
from inventory.states import UnitState


@pytest.mark.django_db
def test_reminder_is_sent_once_per_expiry(make_unit):
    """Ідемпотентність.

    Команду можна ставити в cron хоч щогодини: клієнт отримає одне
    нагадування на один строк. Перезапуск після збою не задублює.
    """
    make_unit("R-1", state=UnitState.ISSUED, expires_in_days=5)

    first = services.send_renewal_reminders(days=14)
    second = services.send_renewal_reminders(days=14)

    assert len(first) == 1
    assert second == []
    assert ReminderLog.objects.count() == 1


@pytest.mark.django_db
def test_renewal_opens_a_new_reminder_window(make_unit):
    """Після продовження строк інший, тому нагадати можна знову.

    Ключ ідемпотентності це (одиниця, тип, строк), а не просто одиниця.
    Інакше клієнт, що продовжив один раз, більше ніколи б не отримав
    нагадування.
    """
    make_unit("R-2", state=UnitState.ISSUED, expires_in_days=3)
    services.send_renewal_reminders(days=14)

    services.renew_unit(unit_ref="R-2", period_days=10, price_cents=100)
    again = services.send_renewal_reminders(days=14)

    assert len(again) == 1
    assert ReminderLog.objects.count() == 2


@pytest.mark.django_db
def test_far_future_units_are_not_reminded(make_unit):
    make_unit("R-3", state=UnitState.ISSUED, expires_in_days=200)
    assert services.send_renewal_reminders(days=14) == []


@pytest.mark.django_db
def test_available_units_are_not_reminded(make_unit):
    """Вільна одиниця нікому не видана, нагадувати нема кому."""
    make_unit("R-4", state=UnitState.AVAILABLE, expires_in_days=3)
    assert services.send_renewal_reminders(days=14) == []


@pytest.mark.django_db
def test_sweep_moves_stale_units_to_expired(make_unit):
    make_unit("S-1", state=UnitState.ISSUED, expires_in_days=-1)
    make_unit("S-2", state=UnitState.ISSUED, expires_in_days=5)

    moved = services.sweep_expired()

    from inventory.models import Unit

    assert moved == 1
    assert Unit.objects.get(ref="S-1").state == UnitState.EXPIRED
    assert Unit.objects.get(ref="S-2").state == UnitState.ISSUED
    assert Event.objects.filter(action="unit.state_changed").count() == 1


@pytest.mark.django_db
def test_dry_run_changes_nothing(make_unit):
    make_unit("R-5", state=UnitState.ISSUED, expires_in_days=2)
    out = StringIO()

    call_command("send_renewal_reminders", "--days", "14", "--dry-run", stdout=out)

    assert "R-5" in out.getvalue()
    assert ReminderLog.objects.count() == 0


@pytest.mark.django_db
def test_command_sends_and_reports(make_unit):
    make_unit("R-6", state=UnitState.ISSUED, expires_in_days=2)
    out = StringIO()

    call_command("send_renewal_reminders", "--days", "14", stdout=out)

    assert "надіслано нагадувань: 1" in out.getvalue()
    assert ReminderLog.objects.count() == 1


@pytest.mark.django_db
def test_long_dead_units_are_not_reminded(make_unit):
    """П'ятий дефект: вікно було однобічним.

    Умова «строк <= зараз + 14 днів» істинна і для одиниці, що протухла
    три роки тому, тому в розсилку падав увесь архів. Тепер вікно має і
    нижню межу.
    """
    make_unit("R-DEAD", state=UnitState.EXPIRED, expires_in_days=-1095)
    make_unit("R-FRESH", state=UnitState.EXPIRED, expires_in_days=-3)

    sent = services.send_renewal_reminders(days=14)

    assert [u.ref for u in sent] == ["R-FRESH"]


@pytest.mark.django_db
def test_grace_window_is_configurable(make_unit):
    make_unit("R-OLD", state=UnitState.EXPIRED, expires_in_days=-100)

    assert services.send_renewal_reminders(days=14) == []
    assert [u.ref for u in services.send_renewal_reminders(days=14, grace_days=365)] == ["R-OLD"]


@pytest.mark.django_db
def test_sweep_command_moves_and_is_idempotent(make_unit):
    """Шостий дефект: логіка підмітання була, запустити її в cron було нічим."""
    make_unit("S-CMD", state=UnitState.ISSUED, expires_in_days=-1)
    out = StringIO()

    call_command("sweep_expired", stdout=out)
    assert "переведено в expired: 1" in out.getvalue()

    second = StringIO()
    call_command("sweep_expired", stdout=second)
    assert "переведено в expired: 0" in second.getvalue()


@pytest.mark.django_db
def test_sweep_dry_run_changes_nothing(make_unit):
    unit = make_unit("S-DRY", state=UnitState.ISSUED, expires_in_days=-1)
    out = StringIO()

    call_command("sweep_expired", "--dry-run", stdout=out)

    unit.refresh_from_db()
    assert "S-DRY" in out.getvalue()
    assert unit.state == UnitState.ISSUED
