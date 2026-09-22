"""Inventory business logic.

Views are deliberately thin: they translate HTTP into a call down here and
back. All the logic lives here, so it can be tested without HTTP and called
the same way from a management command, from the admin, or from a view.

Every function that changes state does so inside one transaction and writes
an event to the audit log. There is no path that changes state silently.
"""

from datetime import datetime, timedelta

from django.db import transaction
from django.db.models import QuerySet
from django.utils import timezone

from .models import (
    Client,
    Event,
    IdempotencyRecord,
    Issue,
    Notification,
    ReminderLog,
    Renewal,
    Unit,
    WarrantyClaim,
)
from .states import ClaimState, UnitState, can_move


class DomainError(Exception):
    """A business rule was violated. Expected, not a bug."""


class IllegalTransition(DomainError):
    pass


class UnitNotAvailable(DomainError):
    pass


class WarrantyExpired(DomainError):
    pass


class UnitExpired(DomainError):
    pass


class IssueClosed(DomainError):
    pass


class ClaimAlreadyOpen(DomainError):
    pass


class IdempotencyConflict(DomainError):
    """The same key was reused with a different payload."""


#: Domain bounds, enforced in the service rather than only in a serializer.
#: The service is the entry point for the API, for management commands and
#: for the admin, so a direct call with a bad value has to produce a business
#: error, not an IntegrityError or an OverflowError turning into a 500.
MAX_CENTS = 2_147_483_647
MAX_PERIOD_DAYS = 3650
MAX_WARRANTY_DAYS = 365


def _check_money(value: int, name: str) -> None:
    if not 0 <= value <= MAX_CENTS:
        raise DomainError(f"{name} must be between 0 and {MAX_CENTS} cents")


def log(action: str, *, actor: str, unit=None, issue=None, **payload) -> Event:
    return Event.objects.create(action=action, actor=actor, unit=unit, issue=issue, payload=payload)


def move_state(unit: Unit, target: str, *, actor: str, reason: str = "") -> Unit:
    """The single place where a unit changes state."""
    if unit.state == target:
        return unit
    if not can_move(unit.state, target):
        raise IllegalTransition(f"{unit.state} -> {target} is not allowed")
    previous = unit.state
    unit.state = target
    unit.save(update_fields=["state", "updated_at"])
    log(
        "unit.state_changed",
        actor=actor,
        unit=unit,
        **{"from": previous, "to": target, "reason": reason},
    )
    return unit


@transaction.atomic
def issue_unit(
    *,
    unit_ref: str,
    client_id: int,
    price_cents: int,
    warranty_days: int = 7,
    actor: str = "system",
) -> Issue:
    """Issue a unit to a client.

    ``select_for_update`` holds the row until the transaction ends, so two
    concurrent requests for the same unit cannot both see it as free. The
    partial unique index in the database is the second line of defence, for
    the day somebody calls the logic around this function.
    """
    _check_money(price_cents, "price")
    if not 0 <= warranty_days <= MAX_WARRANTY_DAYS:
        raise DomainError(f"warranty must be between 0 and {MAX_WARRANTY_DAYS} days")

    unit = Unit.objects.select_for_update().get(ref=unit_ref)
    client = Client.objects.get(pk=client_id)

    if unit.state not in (UnitState.AVAILABLE, UnitState.RESERVED):
        raise UnitNotAvailable(f"unit {unit_ref} is in state {unit.state}")

    now = timezone.now()
    # A free unit past its expiry date is dead stock. The AVAILABLE state
    # only says "not issued to anyone"; it says nothing about whether the
    # unit still works. Without this check the client pays and receives a
    # licence that already stopped working.
    if unit.expires_at and unit.expires_at <= now:
        raise UnitExpired(f"unit {unit_ref} expired on {unit.expires_at:%Y-%m-%d}, renew it first")
    issue = Issue.objects.create(
        unit=unit,
        client=client,
        issued_at=now,
        warranty_days=warranty_days,
        warranty_until=now + timedelta(days=warranty_days),
        price_cents=price_cents,
    )
    move_state(unit, UnitState.ISSUED, actor=actor, reason="issued")
    log(
        "issue.created",
        actor=actor,
        unit=unit,
        issue=issue,
        client=client.name,
        price_cents=price_cents,
    )
    return issue


@transaction.atomic
def renew_unit(
    *,
    unit_ref: str,
    period_days: int,
    price_cents: int,
    actor: str = "system",
    idempotency_key: str = "",
) -> Renewal:
    """Extend a unit's expiry date.

    ``idempotency_key`` makes a retry safe. The ordinary failure it exists
    for has nothing to do with concurrency: the server renews the licence,
    the response is lost on the way back, and the client retries. A row lock
    does not help, because the second call is a legitimate separate request
    that happens to mean the same thing. With a key, the second call returns
    the first result instead of extending the term twice.

    The same key with different arguments is a client bug and is refused
    rather than silently answered with the old result.

    The extension counts from ``max(now, current expiry)``, not from ``now``.
    Otherwise a client who renews a week before the end silently loses those
    seven paid days. This is the most common mistake in this kind of logic,
    so it has a test of its own.
    """
    # The serializer catches these over HTTP, but the service is also called
    # from a management command and from the admin. Without them a direct
    # call turned a bad value into an IntegrityError or an OverflowError,
    # both of which reach the client as a 500 rather than a 409.
    if not 1 <= period_days <= MAX_PERIOD_DAYS:
        raise DomainError(f"a renewal must be between 1 and {MAX_PERIOD_DAYS} days")
    _check_money(price_cents, "price")

    fingerprint = f"{unit_ref}:{period_days}:{price_cents}"
    if idempotency_key:
        seen = IdempotencyRecord.objects.filter(key=idempotency_key).first()
        if seen is not None:
            if seen.fingerprint != fingerprint:
                raise IdempotencyConflict(
                    f"key {idempotency_key} was already used with different arguments"
                )
            return seen.renewal

    unit = Unit.objects.select_for_update().get(ref=unit_ref)
    if unit.state == UnitState.REVOKED:
        raise IllegalTransition("a revoked unit is not renewed")

    now = timezone.now()
    base = unit.expires_at if unit.expires_at and unit.expires_at > now else now
    previous = unit.expires_at
    unit.expires_at = base + timedelta(days=period_days)
    unit.save(update_fields=["expires_at", "updated_at"])

    if unit.state == UnitState.EXPIRED:
        move_state(unit, UnitState.ISSUED, actor=actor, reason="renewed")

    renewal = Renewal.objects.create(
        unit=unit,
        period_days=period_days,
        price_cents=price_cents,
        previous_expires_at=previous,
        new_expires_at=unit.expires_at,
    )
    if idempotency_key:
        # Inside the same transaction as the renewal: either both land or
        # neither does. A key stored after a commit would be lost exactly
        # when it is needed, on a crash between the two writes.
        IdempotencyRecord.objects.create(
            key=idempotency_key, fingerprint=fingerprint, renewal=renewal
        )

    log(
        "unit.renewed",
        actor=actor,
        unit=unit,
        period_days=period_days,
        price_cents=price_cents,
        new_expires_at=unit.expires_at.isoformat(),
    )
    return renewal


@transaction.atomic
def open_claim(*, issue_id: int, reason: str, actor: str = "system") -> WarrantyClaim:
    """Accept a warranty claim if the warranty window is still open.

    Three checks where one looks like it should be enough. Each of them
    closes a separate route to a free replacement:

    1. The window itself. The obvious one.
    2. The issue is still active. Without this, a closed issue could be
       claimed a second time: the old unit is already REVOKED, moving it to
       REVOKED again is a no-op, and the service happily handed out another
       replacement. One paid issue produced two free units.
    3. No open claim exists yet. Otherwise the same hole is reached with two
       parallel claims filed before the first one is approved.

    ``select_for_update`` is here for case 3: without it two concurrent
    requests would both see zero open claims.
    """
    issue = Issue.objects.select_for_update().select_related("unit").get(pk=issue_id)
    now = timezone.now()

    if now > issue.warranty_until:
        raise WarrantyExpired(f"warranty on issue {issue_id} ended {issue.warranty_until:%Y-%m-%d %H:%M}")
    if not issue.is_active:
        raise IssueClosed(f"issue {issue_id} is closed, it cannot be claimed")
    if issue.claims.filter(state=ClaimState.OPEN).exists():
        raise ClaimAlreadyOpen(f"issue {issue_id} already has an open claim")

    claim = WarrantyClaim.objects.create(issue=issue, reason=reason)
    log("claim.opened", actor=actor, unit=issue.unit, issue=issue, reason=reason)
    return claim


@transaction.atomic
def approve_claim(*, claim_id: int, replacement_ref: str, actor: str = "system") -> Issue:
    """Approve a claim: revoke the old unit and issue a replacement.

    The replacement gets a fresh warranty counted from the replacement date.
    The old issue is closed but never deleted: the history has to stay whole.

    The claim row is locked, and that lock is the whole point. Without it two
    concurrent approvals of the same claim with different replacement refs
    both read state=open, both pass the check, and both issue a free unit.
    Neither index catches that: the two replacements are different units, so
    the partial index on active issues is satisfied, and no second open claim
    is ever created, so the one_open_claim_per_issue index is satisfied too.
    One payment, two free replacements, exactly the hole this project claims
    to have closed.
    """
    claim = (
        WarrantyClaim.objects.select_for_update()
        .select_related("issue__unit", "issue__client")
        .get(pk=claim_id)
    )
    if claim.state != ClaimState.OPEN:
        raise DomainError(f"claim {claim_id} is already {claim.state}")

    old_issue = claim.issue
    old_unit = old_issue.unit

    old_issue.is_active = False
    old_issue.closed_at = timezone.now()
    old_issue.save(update_fields=["is_active", "closed_at", "updated_at"])
    move_state(old_unit, UnitState.REVOKED, actor=actor, reason=f"claim {claim_id}")

    new_issue = issue_unit(
        unit_ref=replacement_ref,
        client_id=old_issue.client_id,
        price_cents=0,
        warranty_days=old_issue.warranty_days,
        actor=actor,
    )

    claim.state = ClaimState.APPROVED
    claim.replacement_unit = new_issue.unit
    claim.resolved_at = timezone.now()
    claim.save(update_fields=["state", "replacement_unit", "resolved_at", "updated_at"])
    log(
        "claim.approved",
        actor=actor,
        unit=new_issue.unit,
        issue=new_issue,
        replaced=old_unit.ref,
    )
    return new_issue


@transaction.atomic
def reject_claim(*, claim_id: int, actor: str = "system") -> WarrantyClaim:
    claim = (
        WarrantyClaim.objects.select_for_update().select_related("issue__unit").get(pk=claim_id)
    )
    if claim.state != ClaimState.OPEN:
        raise DomainError(f"claim {claim_id} is already {claim.state}")
    claim.state = ClaimState.REJECTED
    claim.resolved_at = timezone.now()
    claim.save(update_fields=["state", "resolved_at", "updated_at"])
    # The unit is passed so that /api/events/?unit_ref=... answers the
    # question "what happened to my account" completely. Without it a
    # rejection was the one event missing from that history.
    log("claim.rejected", actor=actor, unit=claim.issue.unit, issue=claim.issue)
    return claim


#: How many days past its expiry a unit is still considered alive.
#: Beyond that it is dead stock: reminding somebody to renew a licence that
#: expired two years ago means spamming a customer who left long ago.
DEFAULT_GRACE_DAYS = 30


def expiring_units(days: int, *, grace_days: int = DEFAULT_GRACE_DAYS) -> QuerySet[Unit]:
    """Units whose expiry falls within the next ``days`` days.

    The window has two edges. The upper one is obvious: ``now + days``. The
    lower one is less obvious and matters more: without it the whole archive
    falls into the queryset, because "expiry <= now + 14 days" is also true
    for the year 2019.

    Issued and expired units only. A free unit is not held by anyone, so
    there is nobody to remind.
    """
    now = timezone.now()
    return Unit.objects.filter(
        state__in=[UnitState.ISSUED, UnitState.EXPIRED],
        expires_at__isnull=False,
        expires_at__gte=now - timedelta(days=grace_days),
        expires_at__lte=now + timedelta(days=days),
    ).order_by("expires_at")


def stale_issued_units() -> QuerySet[Unit]:
    """Issued units that are already past expiry. No locking, for previews."""
    return Unit.objects.filter(
        state=UnitState.ISSUED, expires_at__isnull=False, expires_at__lte=timezone.now()
    ).order_by("expires_at")


@transaction.atomic
def sweep_expired(*, actor: str = "system") -> int:
    """Move issued units whose term ran out into EXPIRED.

    The only place where a state change bypasses ``move_state``, and that is
    deliberate: a sweep touches an unbounded number of rows, and a per-row
    save plus a per-row event write would cost two queries per unit.

    The invariant still holds. The ISSUED -> EXPIRED transition is checked
    against the whitelist in ``test_transition_whitelist``, and the events
    are written in the same batch inside the same transaction. The test
    ``test_sweep_writes_one_event_per_unit`` keeps the number of events
    equal to the number of units moved.
    """
    now = timezone.now()
    stale = list(
        Unit.objects.select_for_update().filter(
            state=UnitState.ISSUED, expires_at__isnull=False, expires_at__lte=now
        )
    )
    if not stale:
        return 0

    Unit.objects.filter(pk__in=[u.pk for u in stale]).update(state=UnitState.EXPIRED, updated_at=now)
    Event.objects.bulk_create(
        [
            Event(
                action="unit.state_changed",
                actor=actor,
                unit=u,
                payload={
                    "from": UnitState.ISSUED,
                    "to": UnitState.EXPIRED,
                    "reason": "term ran out",
                },
            )
            for u in stale
        ]
    )
    return len(stale)


@transaction.atomic
def send_renewal_reminders(
    *, days: int, grace_days: int = DEFAULT_GRACE_DAYS, actor: str = "system"
) -> list[Unit]:
    """Send renewal reminders, exactly once per expiry date.

    Idempotency is held by ``ReminderLog`` with a unique key of
    (unit, kind, expiry). A second run sends nothing, so the cron entry can
    fire more often than once a day without producing duplicates.

    The query count is constant and does not depend on the size of the
    queryset. The naive version called ``get_or_create`` and wrote an event
    per unit, roughly five queries each: on ten thousand licences that is
    tens of thousands of round trips per cron run.

    ``select_for_update`` serialises two cron runs that started at the same
    moment: without it both would read an empty ReminderLog and both would
    report the reminder as sent, while only one row landed in the database.
    """
    units = list(expiring_units(days, grace_days=grace_days).select_for_update())
    if not units:
        return []

    # expiring_units filters on expires_at__isnull=False, but a queryset
    # filter does not narrow the field type. The value is pulled out once,
    # with the narrowing in the comprehension, instead of scattering asserts
    # through the batch builders below.
    expiries: dict[int, datetime] = {
        u.id: u.expires_at for u in units if u.expires_at is not None
    }

    already = set(
        ReminderLog.objects.filter(kind="renewal", unit__in=units).values_list("unit_id", "for_expires_at")
    )
    fresh = [u for u in units if (u.id, expiries[u.id]) not in already]
    if not fresh:
        return []

    ReminderLog.objects.bulk_create(
        [ReminderLog(unit=u, kind="renewal", for_expires_at=expiries[u.id]) for u in fresh]
    )
    # The intent is recorded here, in the same transaction as the reminder
    # log. Delivery happens afterwards, in inventory/delivery.py, because a
    # provider call inside a transaction holds row locks for the length of a
    # network round trip.
    #
    # The event is called "queued", not "sent". The old name claimed a
    # delivery that never happened, and an audit log that claims delivery
    # cannot answer "did the client know".
    Notification.objects.bulk_create(
        [Notification(unit=u, kind="renewal") for u in fresh]
    )
    Event.objects.bulk_create(
        [
            Event(
                action="reminder.queued",
                actor=actor,
                unit=u,
                payload={"expires_at": expiries[u.id].isoformat()},
            )
            for u in fresh
        ]
    )
    return fresh
