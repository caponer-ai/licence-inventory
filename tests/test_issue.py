"""Видача одиниці клієнту."""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from inventory import services
from inventory.models import Event, Issue
from inventory.states import UnitState


@pytest.mark.django_db
def test_issue_moves_unit_and_sets_warranty(make_unit, client_rec):
    make_unit("U-1")
    issue = services.issue_unit(
        unit_ref="U-1", client_id=client_rec.id, price_cents=35000
    )

    assert issue.unit.state == UnitState.ISSUED
    assert issue.price_cents == 35000
    delta = issue.warranty_until - issue.issued_at
    assert delta == timedelta(days=7)


@pytest.mark.django_db
def test_cannot_issue_same_unit_twice(make_unit, client_rec):
    make_unit("U-2")
    services.issue_unit(unit_ref="U-2", client_id=client_rec.id, price_cents=100)

    with pytest.raises(services.UnitNotAvailable):
        services.issue_unit(unit_ref="U-2", client_id=client_rec.id, price_cents=100)


@pytest.mark.django_db
def test_database_blocks_second_active_issue_even_past_the_service(
    make_unit, client_rec
):
    """Друга лінія захисту.

    Сервіс перевіряє стан, але якщо хтось колись створить Issue напряму
    (скрипт міграції, адмінка, чужий код), цілісність має врятувати БД.
    Тому на пару (одиниця, активна видача) стоїть частковий унікальний
    індекс, і цей тест перевіряє саме його, а не логіку сервісу.
    """
    unit = make_unit("U-3")
    services.issue_unit(unit_ref="U-3", client_id=client_rec.id, price_cents=100)

    now = timezone.now()
    with pytest.raises(IntegrityError), transaction.atomic():
        Issue.objects.create(
            unit=unit,
            client=client_rec,
            issued_at=now,
            warranty_until=now + timedelta(days=7),
            price_cents=100,
        )


@pytest.mark.django_db
def test_revoked_unit_cannot_be_issued(make_unit, client_rec):
    make_unit("U-4", state=UnitState.REVOKED)
    with pytest.raises(services.UnitNotAvailable):
        services.issue_unit(unit_ref="U-4", client_id=client_rec.id, price_cents=100)


@pytest.mark.django_db
def test_issue_writes_audit_trail(make_unit, client_rec):
    """Жодної зміни стану без сліду в журналі."""
    make_unit("U-5")
    services.issue_unit(
        unit_ref="U-5", client_id=client_rec.id, price_cents=65000, actor="olha"
    )

    actions = set(Event.objects.values_list("action", flat=True))
    assert {"unit.state_changed", "issue.created"} <= actions
    assert Event.objects.filter(actor="olha").exists()


@pytest.mark.django_db
def test_reserved_unit_can_be_issued(make_unit, client_rec):
    make_unit("U-6", state=UnitState.RESERVED)
    issue = services.issue_unit(
        unit_ref="U-6", client_id=client_rec.id, price_cents=100
    )
    assert issue.unit.state == UnitState.ISSUED
