"""Sending the reminders the service decides to send.

Split from ``services.py`` on purpose. The decision ("this licence needs a
reminder") belongs in a database transaction; the delivery ("call the
provider") must not, because a network round trip inside a transaction holds
row locks for as long as the provider takes to answer.

So the reminder job records intent and commits. This module picks the intent
up afterwards and talks to a provider. Nothing is marked sent until a
provider acknowledges it.

The provider here is a no-op that logs. Swapping it for email or a messenger
is one class, and the states, retries and the timeout case already exist
around it.
"""

import logging

from django.db import transaction
from django.utils import timezone

from .models import Notification

logger = logging.getLogger(__name__)

#: How many times a pending notification is retried before it is parked as
#: failed. Parked, not dropped: a human can see it and decide.
MAX_ATTEMPTS = 3


class DeliveryError(Exception):
    """The provider refused or did not answer. Retryable."""


class Provider:
    """What a real email or messenger backend has to implement."""

    def send(self, notification: Notification) -> str:
        """Deliver, or raise DeliveryError. Returns the provider's id."""
        raise NotImplementedError


class LoggingProvider(Provider):
    """The default: writes to the log and reports success.

    Honest about what it is. It exists so the delivery pipeline is complete
    and testable end to end, not so the README can claim messages are sent.
    """

    def send(self, notification: Notification) -> str:
        logger.info(
            "reminder for unit %s expiring %s",
            notification.unit.ref,
            notification.unit.expires_at,
        )
        return f"log-{notification.pk}"


def deliver_pending(*, provider: Provider | None = None, limit: int = 100) -> dict[str, int]:
    """Try to deliver everything still pending.

    Each notification is committed on its own. One provider failure must not
    roll back the deliveries that already succeeded, which is exactly what a
    single transaction around the loop would do.

    A notification that has burned its attempts is marked failed rather than
    retried forever. The timeout case is deliberately counted as a failed
    attempt and left pending: the provider may or may not have accepted it,
    and the only honest position is "unknown, will try again".
    """
    provider = provider or LoggingProvider()
    result = {"sent": 0, "failed": 0, "gave_up": 0}

    pending = list(
        Notification.objects.filter(state=Notification.State.PENDING)
        .select_related("unit")
        .order_by("created_at")[:limit]
    )

    for note in pending:
        try:
            with transaction.atomic():
                locked = Notification.objects.select_for_update().get(pk=note.pk)
                if locked.state != Notification.State.PENDING:
                    continue
                locked.attempts += 1
                try:
                    provider.send(locked)
                except DeliveryError as exc:
                    locked.last_error = str(exc)[:255]
                    if locked.attempts >= MAX_ATTEMPTS:
                        locked.state = Notification.State.FAILED
                        result["gave_up"] += 1
                    else:
                        result["failed"] += 1
                    locked.save(update_fields=["attempts", "last_error", "state"])
                    continue

                locked.state = Notification.State.SENT
                locked.sent_at = timezone.now()
                locked.last_error = ""
                locked.save(update_fields=["state", "sent_at", "attempts", "last_error"])
                result["sent"] += 1
        except Notification.DoesNotExist:  # pragma: no cover - deleted mid-run
            continue

    return result
