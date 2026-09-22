"""The number of database queries must not grow with the data.

Naive reminders made about five queries per unit. On ten units that is 51
queries and nobody notices. On ten thousand licences it is tens of
thousands of round trips per cron run, and production notices instead of
the test suite.

So what is checked here is not speed (that depends on the machine) but the
shape of the dependency: how many queries for 5 units and how many for 40.
If the numbers match, there is no dependency on volume.
"""

from datetime import timedelta

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from inventory import services
from inventory.models import Event, ReminderLog, Unit
from inventory.states import UnitState

#: Exact numbers, not "at most". If an extra query appears, the test has to
#: fail rather than silently let the regression through.
EXPECTED_REMIND = 4
EXPECTED_REMIND_REPEAT = 2
EXPECTED_SWEEP = 3

#: Transaction control is not counted. Django wraps every test in a
#: transaction, and the number of SAVEPOINT/RELEASE statements depends on
#: the backend, so exact numbers including them would be right on SQLite
#: and wrong on Postgres. CI runs both, so the test has to be backend
#: independent.
TRANSACTION_NOISE = ("SAVEPOINT", "RELEASE", "ROLLBACK", "BEGIN", "COMMIT")


def real_queries(captured) -> list[str]:
    return [q["sql"] for q in captured if not q["sql"].upper().lstrip().startswith(TRANSACTION_NOISE)]


class count_queries(CaptureQueriesContext):
    """A query counter without the transaction noise."""

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
    """A repeat cron run must not pay for what was already sent."""
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
    """The safety net for the batched sweep.

    ``sweep_expired`` is the only place where state changes bypass
    ``move_state``. The invariant "every state change leaves a trace" is
    held by this test.
    """
    make_many("EV", 7, days=-1)

    moved = services.sweep_expired(actor="cron")

    events = Event.objects.filter(action="unit.state_changed", actor="cron")
    assert moved == 7
    assert events.count() == 7
    assert {e.payload["to"] for e in events} == {UnitState.EXPIRED}


@pytest.mark.django_db
def test_bulk_reminders_stay_idempotent_across_many_units():
    """The batched version must not lose the main property of the naive one."""
    make_many("ID", 25, days=5)

    first = services.send_renewal_reminders(days=14)
    second = services.send_renewal_reminders(days=14)

    assert len(first) == 25
    assert second == []
    assert ReminderLog.objects.count() == 25
