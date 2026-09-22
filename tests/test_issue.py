"""Issuing a unit to a client."""

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
    issue = services.issue_unit(unit_ref="U-1", client_id=client_rec.id, price_cents=35000)

    assert issue.unit.state == UnitState.ISSUED
    assert issue.price_cents == 35000
    assert issue.warranty_until - issue.issued_at == timedelta(days=7)


@pytest.mark.django_db
def test_cannot_issue_same_unit_twice(make_unit, client_rec):
    make_unit("U-2")
    services.issue_unit(unit_ref="U-2", client_id=client_rec.id, price_cents=100)

    with pytest.raises(services.UnitNotAvailable):
        services.issue_unit(unit_ref="U-2", client_id=client_rec.id, price_cents=100)


@pytest.mark.django_db
def test_database_blocks_second_active_issue_even_past_the_service(make_unit, client_rec):
    """The second line of defence.

    The service checks the state, but the service can be bypassed: a data
    migration, the admin, somebody else's code. Integrity then has to be
    saved by the database. Hence the partial unique index on (unit, active
    issue), and this test exercises the index rather than the logic above it.
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
    """No state change without a trace in the log."""
    make_unit("U-5")
    services.issue_unit(unit_ref="U-5", client_id=client_rec.id, price_cents=65000, actor="olha")

    actions = set(Event.objects.values_list("action", flat=True))
    assert {"unit.state_changed", "issue.created"} <= actions
    assert Event.objects.filter(actor="olha").exists()


@pytest.mark.django_db
def test_reserved_unit_can_be_issued(make_unit, client_rec):
    make_unit("U-6", state=UnitState.RESERVED)
    issue = services.issue_unit(unit_ref="U-6", client_id=client_rec.id, price_cents=100)
    assert issue.unit.state == UnitState.ISSUED
