"""Extending a unit's lifetime.

The key test in this file is ``test_renewal_does_not_eat_paid_days``. That
is the most common mistake in this kind of logic, and the one that makes
clients write to support.
"""

from datetime import timedelta

import pytest
from django.utils import timezone

from inventory import services
from inventory.models import Renewal
from inventory.states import UnitState


@pytest.mark.django_db
def test_renewal_does_not_eat_paid_days(make_unit):
    """A renewal adds to the current term, not to "now".

    The client renews ten days before the end, for 365 days. The correct
    result is 375 days from today. A naive ``now + 365`` silently eats the
    ten days that were already paid for.
    """
    unit = make_unit("U-RENEW", state=UnitState.ISSUED, expires_in_days=10)
    before = unit.expires_at

    services.renew_unit(unit_ref="U-RENEW", period_days=365, price_cents=20000)

    unit.refresh_from_db()
    assert unit.expires_at - before == timedelta(days=365)

    # And the date really is far ahead: the ten remaining days plus 365.
    assert unit.expires_at > timezone.now() + timedelta(days=374)


@pytest.mark.django_db
def test_renewal_of_expired_counts_from_now(make_unit):
    """If the term already ran out, the count starts at "now", not in the past.

    Otherwise renewing a 40-day-old expiry by 30 days would produce a date
    that is also in the past, and the unit would stay dead after payment.
    """
    unit = make_unit("U-STALE", state=UnitState.EXPIRED, expires_in_days=-40)

    before = timezone.now()
    services.renew_unit(unit_ref="U-STALE", period_days=30, price_cents=20000)
    after = timezone.now()

    unit.refresh_from_db()
    # An exact corridor instead of comparing against "now": the count
    # started somewhere between before and after, so the new date sits
    # exactly in that range. A check using .days would flicker on the
    # millisecond boundary.
    assert before + timedelta(days=30) <= unit.expires_at <= after + timedelta(days=30)


@pytest.mark.django_db
def test_renewal_returns_expired_unit_to_issued(make_unit):
    unit = make_unit("U-BACK", state=UnitState.EXPIRED, expires_in_days=-1)
    services.renew_unit(unit_ref="U-BACK", period_days=30, price_cents=100)
    unit.refresh_from_db()
    assert unit.state == UnitState.ISSUED


@pytest.mark.django_db
def test_renewal_records_both_dates(make_unit):
    """The history has to explain where the new date came from."""
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
    """A unit with no expiry: count from "now", and do not crash."""
    from inventory.models import Unit

    make_unit("U-FRESH", state=UnitState.ISSUED, expires_in_days=None)

    before = timezone.now()
    services.renew_unit(unit_ref="U-FRESH", period_days=365, price_cents=9900)
    after = timezone.now()

    expires_at = Unit.objects.get(ref="U-FRESH").expires_at
    assert before + timedelta(days=365) <= expires_at <= after + timedelta(days=365)


@pytest.mark.django_db
def test_zero_day_renewal_is_refused(make_unit):
    """An empty renewal would litter the history with a row about nothing.

    The serializer catches this over HTTP, but the service is also called
    from a management command and from the admin, so the rule lives in the
    service.
    """
    make_unit("U-ZERO", state=UnitState.ISSUED, expires_in_days=5)

    with pytest.raises(services.DomainError):
        services.renew_unit(unit_ref="U-ZERO", period_days=0, price_cents=0)

    assert Renewal.objects.count() == 0
