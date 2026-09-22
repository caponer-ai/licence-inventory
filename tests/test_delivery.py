"""Delivery states, retries, and the case where nobody knows what happened.

Before this the reminder job wrote an event called `reminder.sent` while
nothing was sent. The name was the problem more than the missing feature: an
audit log that claims delivery cannot answer "did the client know".
"""

import pytest

from inventory import delivery, services
from inventory.models import Event, Notification
from inventory.states import UnitState


class AlwaysFails(delivery.Provider):
    def send(self, notification):
        raise delivery.DeliveryError("provider is down")


class FailsThenWorks(delivery.Provider):
    def __init__(self, failures):
        self.left = failures
        self.calls = 0

    def send(self, notification):
        self.calls += 1
        if self.left > 0:
            self.left -= 1
            raise delivery.DeliveryError("temporary")
        return "ok-1"


@pytest.mark.django_db
def test_reminders_queue_intent_and_do_not_claim_delivery(make_unit):
    make_unit("N-1", state=UnitState.ISSUED, expires_in_days=5)

    services.send_renewal_reminders(days=14)

    note = Notification.objects.get(unit__ref="N-1")
    assert note.state == Notification.State.PENDING
    assert note.sent_at is None
    # The event says queued, not sent, because nothing has been sent yet.
    assert Event.objects.filter(action="reminder.queued").exists()
    assert not Event.objects.filter(action="reminder.sent").exists()


@pytest.mark.django_db
def test_delivery_marks_sent_only_after_the_provider_answers(make_unit):
    make_unit("N-2", state=UnitState.ISSUED, expires_in_days=5)
    services.send_renewal_reminders(days=14)

    result = delivery.deliver_pending()

    note = Notification.objects.get(unit__ref="N-2")
    assert result["sent"] == 1
    assert note.state == Notification.State.SENT
    assert note.sent_at is not None
    assert note.attempts == 1


@pytest.mark.django_db
def test_a_failure_stays_pending_and_is_retried(make_unit):
    make_unit("N-3", state=UnitState.ISSUED, expires_in_days=5)
    services.send_renewal_reminders(days=14)

    provider = FailsThenWorks(failures=1)
    first = delivery.deliver_pending(provider=provider)
    second = delivery.deliver_pending(provider=provider)

    note = Notification.objects.get(unit__ref="N-3")
    assert first["failed"] == 1
    assert second["sent"] == 1
    assert note.state == Notification.State.SENT
    assert note.attempts == 2
    assert note.last_error == ""


@pytest.mark.django_db
def test_a_hopeless_notification_is_parked_not_retried_forever(make_unit):
    """Parked rather than dropped: somebody has to be able to see it."""
    make_unit("N-4", state=UnitState.ISSUED, expires_in_days=5)
    services.send_renewal_reminders(days=14)

    provider = AlwaysFails()
    for _ in range(delivery.MAX_ATTEMPTS + 2):
        delivery.deliver_pending(provider=provider)

    note = Notification.objects.get(unit__ref="N-4")
    assert note.state == Notification.State.FAILED
    assert note.attempts == delivery.MAX_ATTEMPTS
    assert "provider is down" in note.last_error


@pytest.mark.django_db
def test_one_failure_does_not_roll_back_the_successful_ones(make_unit):
    """The reason each notification commits on its own.

    A single transaction around the loop would undo everything that already
    went out the moment one provider call failed, and the client who did get
    the message would be told again next run.
    """
    make_unit("N-OK", state=UnitState.ISSUED, expires_in_days=5)
    make_unit("N-BAD", state=UnitState.ISSUED, expires_in_days=5)
    services.send_renewal_reminders(days=14)

    class FailsForOne(delivery.Provider):
        def send(self, notification):
            if notification.unit.ref == "N-BAD":
                raise delivery.DeliveryError("nope")
            return "ok"

    result = delivery.deliver_pending(provider=FailsForOne())

    assert result == {"sent": 1, "failed": 1, "gave_up": 0}
    assert Notification.objects.get(unit__ref="N-OK").state == Notification.State.SENT
    assert Notification.objects.get(unit__ref="N-BAD").state == Notification.State.PENDING


@pytest.mark.django_db
def test_a_second_worker_skips_what_is_already_delivered(make_unit):
    make_unit("N-5", state=UnitState.ISSUED, expires_in_days=5)
    services.send_renewal_reminders(days=14)

    delivery.deliver_pending()
    again = delivery.deliver_pending()

    assert again == {"sent": 0, "failed": 0, "gave_up": 0}
    assert Notification.objects.get(unit__ref="N-5").attempts == 1


@pytest.mark.django_db
def test_command_reports_what_happened(make_unit):
    from io import StringIO

    from django.core.management import call_command

    make_unit("N-6", state=UnitState.ISSUED, expires_in_days=5)
    services.send_renewal_reminders(days=14)
    out = StringIO()

    call_command("deliver_notifications", stdout=out)

    assert "sent 1" in out.getvalue()
