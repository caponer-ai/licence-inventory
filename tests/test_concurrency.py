"""Two requests at the same time.

Everything else in this suite runs sequentially, and sequential tests cannot
see the class of bug that lives here: two transactions reading the same row
before either of them commits.

These use ``transaction=True`` and real threads with their own connections.
They run on Postgres only, and that is the point rather than a dodge.
``select_for_update`` needs row-level locking; SQLite has none and instead
locks the whole database, so the loser gets an ``OperationalError`` at a
moment that depends on thread scheduling. A test that passes for that reason
would be measuring the scheduler, not the lock, and it flickered when tried.

This is what makes the Postgres leg of CI load-bearing instead of
decorative: it is the only place where the main guarantee of the project is
actually exercised under concurrency.
"""

import threading

import pytest
from django.db import connection, connections, transaction

from inventory import services
from inventory.models import Issue, Unit
from inventory.states import ClaimState, UnitState

pytestmark = pytest.mark.skipif(
    connection.vendor != "postgresql",
    reason="row-level locking is required; SQLite locks the whole database instead",
)


def run_in_parallel(fn, args_list):
    """Call ``fn`` once per argument tuple, each on its own connection.

    Every thread closes its connection afterwards, otherwise the test
    database keeps them open and teardown hangs.
    """
    results: list[object] = [None] * len(args_list)

    def worker(index, args):
        try:
            results[index] = fn(*args)
        except Exception as exc:  # noqa: BLE001 - the outcome is the assertion
            results[index] = exc
        finally:
            connections.close_all()

    threads = [threading.Thread(target=worker, args=(i, a)) for i, a in enumerate(args_list)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=20)
    return results


@pytest.mark.django_db(transaction=True)
def test_two_approvals_of_one_claim_produce_one_replacement(django_user_model):
    """The blocker that two independent reviewers found on the same day.

    Without a lock on the claim row both requests read state=open, both pass
    the check, and both issue a free replacement. Neither index catches it:
    the replacements are different units, so the partial index on active
    issues is satisfied, and no second open claim is ever created, so
    one_open_claim_per_issue is satisfied too.

    The invariant this asserts is the one the README states: one payment
    buys exactly one free replacement.
    """
    from inventory.models import Client

    client = Client.objects.create(name="Parallel Ltd")
    for ref in ("C-ORIG", "C-R1", "C-R2"):
        Unit.objects.create(ref=ref)

    issue = services.issue_unit(unit_ref="C-ORIG", client_id=client.id, price_cents=35000)
    claim = services.open_claim(issue_id=issue.id, reason="banned")

    def approve(replacement_ref):
        with transaction.atomic():
            return services.approve_claim(claim_id=claim.id, replacement_ref=replacement_ref)

    results = run_in_parallel(approve, [("C-R1",), ("C-R2",)])

    succeeded = [r for r in results if isinstance(r, Issue)]
    refused = [r for r in results if isinstance(r, services.DomainError)]

    assert len(succeeded) == 1, f"both approvals went through: {results}"
    assert len(refused) == 1, f"the loser must get a domain error, got {results}"

    free = Issue.objects.filter(client=client, price_cents=0).count()
    assert free == 1, "one payment must buy exactly one free replacement"

    claim.refresh_from_db()
    assert claim.state == ClaimState.APPROVED
    # The replacement recorded on the claim is the one that was really issued.
    assert claim.replacement_unit.ref == succeeded[0].unit.ref


@pytest.mark.django_db(transaction=True)
def test_approve_and_reject_cannot_both_win():
    """A parallel reject used to overwrite an approval after the fact.

    The claim ended up REJECTED while a free replacement was already issued,
    so the audit log said the opposite of what happened.
    """
    from inventory.models import Client

    client = Client.objects.create(name="Parallel Ltd")
    for ref in ("C-AR", "C-AR-REPL"):
        Unit.objects.create(ref=ref)

    issue = services.issue_unit(unit_ref="C-AR", client_id=client.id, price_cents=100)
    claim = services.open_claim(issue_id=issue.id, reason="x")

    def approve(_):
        with transaction.atomic():
            return services.approve_claim(claim_id=claim.id, replacement_ref="C-AR-REPL")

    def reject(_):
        with transaction.atomic():
            return services.reject_claim(claim_id=claim.id)

    def either(which):
        return approve(None) if which == "approve" else reject(None)

    results = run_in_parallel(either, [("approve",), ("reject",)])

    refused = [r for r in results if isinstance(r, services.DomainError)]
    assert len(refused) == 1, f"exactly one of the two must lose, got {results}"

    claim.refresh_from_db()
    free = Issue.objects.filter(client=client, price_cents=0).count()
    # The stored outcome and the issued replacement agree with each other.
    # Before the lock they could disagree: a late reject overwrote an
    # approval that had already handed out a free unit.
    if claim.state == ClaimState.APPROVED:
        assert free == 1
    else:
        assert claim.state == ClaimState.REJECTED
        assert free == 0


@pytest.mark.django_db(transaction=True)
def test_two_issues_of_one_unit_produce_one_issue():
    """The oldest invariant of the project, now under a real race.

    Even where select_for_update does nothing, the partial unique index has
    to keep the second writer out.
    """
    from inventory.models import Client

    client = Client.objects.create(name="Parallel Ltd")
    Unit.objects.create(ref="C-ONE")

    def issue(_):
        with transaction.atomic():
            return services.issue_unit(unit_ref="C-ONE", client_id=client.id, price_cents=100)

    results = run_in_parallel(issue, [(1,), (2,)])

    created = [r for r in results if isinstance(r, Issue)]
    assert len(created) == 1, f"the unit was issued twice: {results}"
    assert Issue.objects.filter(unit__ref="C-ONE", is_active=True).count() == 1
    assert Unit.objects.get(ref="C-ONE").state == UnitState.ISSUED
