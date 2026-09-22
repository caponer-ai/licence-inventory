"""The admin, the demo command, and error branches.

These parts usually go untested, because "it's only the admin" and "it's
only demo data". That is exactly why they break quietly: an admin action
changes live data just like an endpoint does, and a command that crashes is
the first thing a new developer meets.
"""

from datetime import timedelta
from io import StringIO
from unittest import mock

import pytest
from django.contrib.admin.sites import AdminSite
from django.contrib.auth.models import User
from django.core.management import call_command
from django.utils import timezone

from inventory import services
from inventory.admin import EventAdmin, UnitAdmin
from inventory.models import Client, Event, Unit
from inventory.states import UnitState


@pytest.fixture
def unit_admin():
    return UnitAdmin(Unit, AdminSite())


@pytest.fixture
def request_with_user(rf, staff):
    request = rf.post("/admin/")
    request.user = staff
    # message_user goes through the messages framework, which needs storage.
    request._messages = mock.MagicMock()
    return request


@pytest.mark.django_db
def test_admin_action_renews_and_leaves_a_trail(unit_admin, request_with_user, make_unit, staff):
    """The admin action must travel the same path as the API.

    If it changed expires_at directly, the admin would stay a hole in the
    very invariant the API closes.
    """
    unit = make_unit("AD-1", state=UnitState.ISSUED, expires_in_days=10)
    before = unit.expires_at

    unit_admin.renew_30_days(request_with_user, Unit.objects.filter(ref="AD-1"))

    unit.refresh_from_db()
    assert unit.expires_at == before + timedelta(days=30)
    assert unit.renewals.count() == 1
    assert Event.objects.filter(action="unit.renewed", actor=staff.username).exists()


@pytest.mark.django_db
def test_admin_action_reports_refusal_instead_of_crashing(unit_admin, request_with_user, make_unit):
    """A revoked unit cannot be renewed, and the admin must not blow up."""
    make_unit("AD-2", state=UnitState.REVOKED)

    unit_admin.renew_30_days(request_with_user, Unit.objects.filter(ref="AD-2"))

    assert Unit.objects.get(ref="AD-2").renewals.count() == 0


@pytest.mark.django_db
def test_admin_action_continues_after_one_failure(unit_admin, request_with_user, make_unit):
    """One bad unit in a batch must not stop the rest."""
    make_unit("AD-BAD", state=UnitState.REVOKED)
    make_unit("AD-OK", state=UnitState.ISSUED, expires_in_days=5)

    unit_admin.renew_30_days(request_with_user, Unit.objects.filter(ref__startswith="AD-"))

    assert Unit.objects.get(ref="AD-OK").renewals.count() == 1


def test_event_log_cannot_be_edited_in_admin():
    """The audit log is append-only, in the admin too."""
    event_admin = EventAdmin(Event, AdminSite())
    assert event_admin.has_add_permission(None) is False
    assert event_admin.has_change_permission(None) is False
    assert event_admin.has_delete_permission(None) is False


def test_unit_admin_locks_state_and_expiry():
    admin = UnitAdmin(Unit, AdminSite())
    assert set(admin.readonly_fields) == {"state", "expires_at"}


@pytest.mark.django_db
def test_seed_demo_builds_a_usable_starting_point():
    """The README promises a working state after one command. Verify it."""
    out = StringIO()

    call_command("seed_demo", stdout=out)

    text = out.getvalue()
    assert Unit.objects.count() == 6
    assert Client.objects.count() == 2
    assert User.objects.filter(username="demo").exists()
    # The token is printed, otherwise the first curl from the README hits 401.
    assert "token:" in text
    assert "curl" in text


@pytest.mark.django_db
def test_seed_demo_refuses_to_run_twice():
    call_command("seed_demo", stdout=StringIO())
    out = StringIO()

    call_command("seed_demo", stdout=out)

    assert "not empty" in out.getvalue()
    assert Unit.objects.count() == 6


@pytest.mark.django_db
def test_healthz_returns_503_when_database_is_down(api_anon):
    """The branch this endpoint was rewritten for.

    What is checked is the behaviour with a dead database, not the presence
    of a try/except: otherwise the test would only confirm that the code
    was written.
    """
    with mock.patch("inventory.views.connection") as fake:
        fake.cursor.side_effect = RuntimeError("database unreachable")
        response = api_anon.get("/healthz/")

    assert response.status_code == 503
    assert response.data["status"] == "error"
    assert response.data["database"] == "RuntimeError"
    # Only the exception type leaves the server, never the message.
    assert "unreachable" not in str(response.data)


@pytest.mark.django_db
def test_reject_claim_over_http(api, make_unit, client_rec):
    make_unit("RJ-1")
    issue = services.issue_unit(unit_ref="RJ-1", client_id=client_rec.id, price_cents=100)
    claim = api.post(f"/api/issues/{issue.id}/claim/", {"reason": "x"}, format="json")

    response = api.post(f"/api/claims/{claim.data['id']}/reject/", format="json")

    assert response.status_code == 200
    assert response.data["state"] == "rejected"


@pytest.mark.django_db
def test_model_str_is_readable(make_unit, client_rec):
    """__str__ shows up in the admin and in logs, so it is a contract too."""
    make_unit("STR-1", expires_in_days=5)
    issue = services.issue_unit(unit_ref="STR-1", client_id=client_rec.id, price_cents=100)
    renewal = services.renew_unit(unit_ref="STR-1", period_days=30, price_cents=100)
    claim = services.open_claim(issue_id=issue.id, reason="x")

    assert str(issue.unit).startswith("STR-1")
    assert str(client_rec) == "Romashka Ltd"
    assert "STR-1" in str(issue)
    assert "STR-1" in str(renewal)
    assert "STR-1" in str(claim)
    assert "unit.renewed" in str(Event.objects.filter(action="unit.renewed").first())


@pytest.mark.django_db
def test_reminder_log_str(make_unit):
    make_unit("RL-1", state=UnitState.ISSUED, expires_in_days=3)
    services.send_renewal_reminders(days=14)

    from inventory.models import ReminderLog

    assert "RL-1" in str(ReminderLog.objects.first())


@pytest.mark.django_db
def test_events_can_be_filtered_by_unit(api, make_unit, client_rec):
    make_unit("EV-A")
    make_unit("EV-B")
    services.issue_unit(unit_ref="EV-A", client_id=client_rec.id, price_cents=100)
    services.issue_unit(unit_ref="EV-B", client_id=client_rec.id, price_cents=100)

    filtered = api.get("/api/events/?unit_ref=EV-A").data["results"]
    everything = api.get("/api/events/").data["results"]

    assert len(filtered) < len(everything)
    assert all(row["unit"] is not None for row in filtered)


@pytest.mark.django_db
def test_dry_run_sweep_lists_without_touching():
    now = timezone.now()
    Unit.objects.create(ref="DR-1", state=UnitState.ISSUED, expires_at=now - timedelta(days=2))
    out = StringIO()

    call_command("sweep_expired", "--dry-run", stdout=out)

    assert "DR-1" in out.getvalue()
    assert Unit.objects.get(ref="DR-1").state == UnitState.ISSUED
