"""Routes by which a client gets more than they paid for.

All 76 previous tests were green when this was found: one paid issue
produced two free replacements.

Holes like this are invisible in line coverage, because every line on its
own behaves correctly. They only show up when a scenario is walked end to
end and the result is read in money.
"""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from inventory import services
from inventory.models import Issue, Unit, WarrantyClaim
from inventory.states import ClaimState, UnitState


@pytest.mark.django_db
def test_closed_issue_cannot_be_claimed_again(make_unit, client_rec):
    """The main find of the round.

    Scenario: issue, claim, approve. The old unit becomes REVOKED, the
    issue is closed, the client gets a replacement. Then, while the
    warranty window is still open, a second claim is filed against the SAME
    old issue. Moving REVOKED to REVOKED is a no-op and passes silently, so
    the service handed out another free replacement.

    In money the outcome was: one paid issue, two free units.
    """
    make_unit("F-ORIG")
    make_unit("F-R1")
    make_unit("F-R2")
    issue = services.issue_unit(unit_ref="F-ORIG", client_id=client_rec.id, price_cents=35000)

    first = services.open_claim(issue_id=issue.id, reason="first")
    services.approve_claim(claim_id=first.id, replacement_ref="F-R1")

    with pytest.raises(services.IssueClosed):
        services.open_claim(issue_id=issue.id, reason="second on the same issue")

    free = Issue.objects.filter(client=client_rec, price_cents=0).count()
    assert free == 1, "exactly one free replacement per payment"
    assert Unit.objects.get(ref="F-R2").state == UnitState.AVAILABLE


@pytest.mark.django_db
def test_second_open_claim_is_refused(make_unit, client_rec):
    """The same hole by a shorter route: two claims before the first approval."""
    make_unit("F-2")
    issue = services.issue_unit(unit_ref="F-2", client_id=client_rec.id, price_cents=100)
    services.open_claim(issue_id=issue.id, reason="first")

    with pytest.raises(services.ClaimAlreadyOpen):
        services.open_claim(issue_id=issue.id, reason="second")


@pytest.mark.django_db
def test_database_refuses_second_open_claim_past_the_service(make_unit, client_rec):
    """Second line, as with issues: the rule also lives in the database."""
    make_unit("F-3")
    issue = services.issue_unit(unit_ref="F-3", client_id=client_rec.id, price_cents=100)
    WarrantyClaim.objects.create(issue=issue, reason="first")

    with pytest.raises(IntegrityError), transaction.atomic():
        WarrantyClaim.objects.create(issue=issue, reason="second, bypassing the service")


@pytest.mark.django_db
def test_rejected_claim_allows_a_new_one(make_unit, client_rec):
    """The guard must not lock the client out forever after one refusal."""
    make_unit("F-4")
    issue = services.issue_unit(unit_ref="F-4", client_id=client_rec.id, price_cents=100)
    first = services.open_claim(issue_id=issue.id, reason="first")
    services.reject_claim(claim_id=first.id)

    second = services.open_claim(issue_id=issue.id, reason="new circumstances")

    assert second.state == ClaimState.OPEN


@pytest.mark.django_db
def test_expired_unit_cannot_be_sold(make_unit, client_rec):
    """AVAILABLE means "not issued to anyone", not "works".

    A unit can be renewed while free, then sit around and go stale. Its
    state stays AVAILABLE. Without a separate check the client pays and
    receives a dead licence.
    """
    Unit.objects.create(
        ref="F-DEAD", state=UnitState.AVAILABLE, expires_at=timezone.now() - timedelta(days=10)
    )

    with pytest.raises(services.UnitExpired):
        services.issue_unit(unit_ref="F-DEAD", client_id=client_rec.id, price_cents=35000)

    assert not Issue.objects.filter(unit__ref="F-DEAD").exists()


@pytest.mark.django_db
def test_unit_with_future_expiry_sells_normally(client_rec):
    Unit.objects.create(ref="F-OK", state=UnitState.AVAILABLE, expires_at=timezone.now() + timedelta(days=30))
    issue = services.issue_unit(unit_ref="F-OK", client_id=client_rec.id, price_cents=100)
    assert issue.unit.state == UnitState.ISSUED


@pytest.mark.django_db
def test_unit_without_expiry_sells_normally(make_unit, client_rec):
    """No expiry set means perpetual, not stale."""
    make_unit("F-NOEXP")
    issue = services.issue_unit(unit_ref="F-NOEXP", client_id=client_rec.id, price_cents=100)
    assert issue.unit.state == UnitState.ISSUED


@pytest.mark.django_db
def test_non_numeric_id_returns_404_not_500(api):
    """DRF lets anything without a slash or a dot through as pk.

    `int("abc")` raised ValueError, the handler did not know it, and the
    client got a 500 instead of a 404.
    """
    assert api.post("/api/issues/abc/claim/", {"reason": "x"}, format="json").status_code == 404
    assert api.post("/api/claims/abc/approve/", {"replacement_ref": "z"}, format="json").status_code == 404
    assert api.post("/api/claims/abc/reject/", format="json").status_code == 404


@pytest.mark.django_db
def test_negative_price_is_a_domain_error_not_a_crash(make_unit, client_rec):
    """The CHECK constraint stops the row, but an IntegrityError is a 500.

    The service is also reached from a command and from the admin, where no
    serializer runs, so the rule lives in the service.
    """
    make_unit("NEG-1")
    with pytest.raises(services.DomainError):
        services.issue_unit(unit_ref="NEG-1", client_id=client_rec.id, price_cents=-500)
    assert Unit.objects.get(ref="NEG-1").state == UnitState.AVAILABLE


@pytest.mark.django_db
def test_absurd_warranty_is_refused(make_unit, client_rec):
    make_unit("NEG-2")
    with pytest.raises(services.DomainError):
        services.issue_unit(
            unit_ref="NEG-2", client_id=client_rec.id, price_cents=100, warranty_days=100000
        )


@pytest.mark.django_db
def test_rejection_is_visible_in_the_unit_history(api, make_unit, client_rec):
    """/api/events/?unit_ref=... answers "what happened to my account".

    The rejection event used to be written without a unit, so it was the one
    thing missing from that answer.
    """
    make_unit("REJ-1")
    issue = services.issue_unit(unit_ref="REJ-1", client_id=client_rec.id, price_cents=100)
    claim = services.open_claim(issue_id=issue.id, reason="x")
    services.reject_claim(claim_id=claim.id)

    actions = [row["action"] for row in api.get("/api/events/?unit_ref=REJ-1").data["results"]]
    assert "claim.rejected" in actions
