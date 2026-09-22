"""Retrying a renewal must not extend the term twice.

The failure this guards against is ordinary rather than exotic: the server
renews the licence, the response is lost on the way back, and the client
retries. A row lock orders operations; it cannot tell that two separate
requests mean the same thing.
"""

import pytest

from inventory import services
from inventory.models import IdempotencyRecord, Renewal, Unit
from inventory.states import UnitState


@pytest.mark.django_db
def test_retry_with_the_same_key_does_not_extend_twice(make_unit):
    unit = make_unit("ID-1", state=UnitState.ISSUED, expires_in_days=10)
    before = unit.expires_at

    first = services.renew_unit(
        unit_ref="ID-1", period_days=365, price_cents=20000, idempotency_key="req-1"
    )
    second = services.renew_unit(
        unit_ref="ID-1", period_days=365, price_cents=20000, idempotency_key="req-1"
    )

    unit.refresh_from_db()
    assert second.pk == first.pk, "the retry created a second renewal"
    assert Renewal.objects.filter(unit=unit).count() == 1
    assert unit.expires_at == before + __import__("datetime").timedelta(days=365)


@pytest.mark.django_db
def test_same_key_with_different_arguments_is_refused(make_unit):
    """Answering a different request with an old result would be worse than
    refusing: the caller would believe something happened that did not."""
    make_unit("ID-2", state=UnitState.ISSUED, expires_in_days=10)
    services.renew_unit(
        unit_ref="ID-2", period_days=30, price_cents=100, idempotency_key="req-2"
    )

    with pytest.raises(services.IdempotencyConflict):
        services.renew_unit(
            unit_ref="ID-2", period_days=365, price_cents=100, idempotency_key="req-2"
        )


@pytest.mark.django_db
def test_different_keys_renew_twice_as_intended(make_unit):
    """Idempotency must not turn into "the second renewal never works"."""
    unit = make_unit("ID-3", state=UnitState.ISSUED, expires_in_days=10)
    before = unit.expires_at

    services.renew_unit(unit_ref="ID-3", period_days=30, price_cents=100, idempotency_key="a")
    services.renew_unit(unit_ref="ID-3", period_days=30, price_cents=100, idempotency_key="b")

    unit.refresh_from_db()
    assert Renewal.objects.filter(unit=unit).count() == 2
    assert unit.expires_at == before + __import__("datetime").timedelta(days=60)


@pytest.mark.django_db
def test_without_a_key_nothing_is_recorded(make_unit):
    """The key is opt-in; callers that do not retry pay nothing for it."""
    make_unit("ID-4", state=UnitState.ISSUED, expires_in_days=10)
    services.renew_unit(unit_ref="ID-4", period_days=30, price_cents=100)
    assert IdempotencyRecord.objects.count() == 0


@pytest.mark.django_db
def test_a_refused_renewal_leaves_no_key_behind(make_unit):
    """The record is written in the same transaction as the renewal.

    If it were written afterwards, a failure in between would leave a key
    pointing at nothing, and the retry it was meant to protect would extend
    the term a second time.
    """
    make_unit("ID-5", state=UnitState.REVOKED)

    with pytest.raises(services.IllegalTransition):
        services.renew_unit(
            unit_ref="ID-5", period_days=30, price_cents=100, idempotency_key="req-5"
        )

    assert not IdempotencyRecord.objects.filter(key="req-5").exists()
    assert Unit.objects.get(ref="ID-5").renewals.count() == 0
