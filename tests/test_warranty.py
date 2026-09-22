"""The warranty window and claims."""

from datetime import timedelta

import pytest
from django.utils import timezone

from inventory import services
from inventory.models import Issue, WarrantyClaim
from inventory.states import ClaimState, UnitState


@pytest.mark.django_db
def test_claim_accepted_inside_window(make_unit, client_rec):
    make_unit("W-1")
    issue = services.issue_unit(unit_ref="W-1", client_id=client_rec.id, price_cents=100, warranty_days=7)
    claim = services.open_claim(issue_id=issue.id, reason="does not work")
    assert claim.state == ClaimState.OPEN


@pytest.mark.django_db
def test_claim_rejected_outside_window(make_unit, client_rec):
    """The edge of the window is exercised, not merely declared.

    We move warranty_until into the past instead of waiting seven days.
    """
    make_unit("W-2")
    issue = services.issue_unit(unit_ref="W-2", client_id=client_rec.id, price_cents=100)
    issue.warranty_until = timezone.now() - timedelta(seconds=1)
    issue.save(update_fields=["warranty_until"])

    with pytest.raises(services.WarrantyExpired):
        services.open_claim(issue_id=issue.id, reason="too late")


@pytest.mark.django_db
def test_approved_claim_revokes_old_and_issues_replacement(make_unit, client_rec):
    make_unit("W-3")
    make_unit("W-3-REPL")
    issue = services.issue_unit(unit_ref="W-3", client_id=client_rec.id, price_cents=35000)
    claim = services.open_claim(issue_id=issue.id, reason="banned")

    new_issue = services.approve_claim(claim_id=claim.id, replacement_ref="W-3-REPL")

    issue.refresh_from_db()
    claim.refresh_from_db()

    assert issue.is_active is False
    assert issue.closed_at is not None
    assert issue.unit.state == UnitState.REVOKED
    assert claim.state == ClaimState.APPROVED
    assert claim.replacement_unit.ref == "W-3-REPL"
    # The replacement goes to the same client, free of charge.
    assert new_issue.client_id == client_rec.id
    assert new_issue.price_cents == 0
    # History stays whole: the old issue did not disappear.
    assert Issue.objects.filter(unit__ref="W-3").count() == 1


@pytest.mark.django_db
def test_claim_cannot_be_resolved_twice(make_unit, client_rec):
    make_unit("W-4")
    make_unit("W-4-A")
    make_unit("W-4-B")
    issue = services.issue_unit(unit_ref="W-4", client_id=client_rec.id, price_cents=100)
    claim = services.open_claim(issue_id=issue.id, reason="x")
    services.approve_claim(claim_id=claim.id, replacement_ref="W-4-A")

    with pytest.raises(services.DomainError):
        services.approve_claim(claim_id=claim.id, replacement_ref="W-4-B")


@pytest.mark.django_db
def test_rejected_claim_leaves_unit_issued(make_unit, client_rec):
    make_unit("W-5")
    issue = services.issue_unit(unit_ref="W-5", client_id=client_rec.id, price_cents=100)
    claim = services.open_claim(issue_id=issue.id, reason="we will look into it")

    services.reject_claim(claim_id=claim.id)

    issue.refresh_from_db()
    assert WarrantyClaim.objects.get(pk=claim.id).state == ClaimState.REJECTED
    assert issue.is_active is True
    assert issue.unit.state == UnitState.ISSUED


@pytest.mark.django_db
def test_claim_cannot_be_rejected_twice(make_unit, client_rec):
    """Mirror of the double approval: rejecting twice is refused too."""
    make_unit("W-6")
    issue = services.issue_unit(unit_ref="W-6", client_id=client_rec.id, price_cents=100)
    claim = services.open_claim(issue_id=issue.id, reason="x")
    services.reject_claim(claim_id=claim.id)

    with pytest.raises(services.DomainError):
        services.reject_claim(claim_id=claim.id)
