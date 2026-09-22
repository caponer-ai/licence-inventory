"""The API contract: what client code leans on.

This file is deliberately separate from `test_api.py`. That one walks the
business path (issue, renew, replace); this one pins the shape and the
error codes. A contract is easier to break than the logic, and the break is
noticed later.

Every test below matches a defect found by trying to break the service
after all the previous tests were already green.
"""

import pytest

from inventory import services
from inventory.models import Unit
from inventory.states import UnitState


@pytest.mark.django_db
def test_delete_unit_with_history_returns_409_not_500(api_staff, make_unit, client_rec):
    """The worst defect of the first round: ProtectedError escaped.

    `on_delete=PROTECT` raises `ProtectedError`, DRF does not know it, so
    the client got a 500 while the server had behaved perfectly correctly.
    """
    make_unit("D-1")
    services.issue_unit(unit_ref="D-1", client_id=client_rec.id, price_cents=100)

    response = api_staff.delete("/api/units/D-1/")

    assert response.status_code == 409
    assert response.data["code"] == "ProtectedError"
    assert Unit.objects.filter(ref="D-1").exists()


@pytest.mark.django_db
def test_delete_client_with_history_returns_409(api_staff, make_unit, client_rec):
    make_unit("D-2")
    services.issue_unit(unit_ref="D-2", client_id=client_rec.id, price_cents=100)

    response = api_staff.delete(f"/api/clients/{client_rec.id}/")

    assert response.status_code == 409


@pytest.mark.django_db
def test_unit_without_history_can_be_deleted(api_staff, make_unit):
    """The guard must not get in the way of the normal case."""
    make_unit("D-3")
    assert api_staff.delete("/api/units/D-3/").status_code == 204


@pytest.mark.django_db
def test_put_cannot_rewrite_expiry_behind_the_log(api, make_unit):
    """Second defect: a plain PUT rewrote the expiry date.

    The date changed, no Renewal row appeared, no audit entry either. There
    would have been nothing left to explain the new date to the client. Now
    expires_at is read-only and `renew` is the only way in.
    """
    unit = make_unit("D-4", state=UnitState.ISSUED, expires_in_days=10)
    before = unit.expires_at

    response = api.put(
        "/api/units/D-4/",
        {"ref": "D-4", "tier": "individual", "cost_cents": 0, "expires_at": "2030-01-01T00:00:00Z"},
        format="json",
    )

    unit.refresh_from_db()
    assert response.status_code == 200
    assert unit.expires_at == before
    assert unit.renewals.count() == 0


@pytest.mark.django_db
def test_put_cannot_rewrite_state(api, make_unit):
    unit = make_unit("D-5", state=UnitState.REVOKED)
    api.put(
        "/api/units/D-5/",
        {"ref": "D-5", "tier": "individual", "cost_cents": 0, "state": "available"},
        format="json",
    )
    unit.refresh_from_db()
    assert unit.state == UnitState.REVOKED


@pytest.mark.django_db
def test_renew_still_works_and_leaves_a_trail(api, make_unit):
    unit = make_unit("D-6", state=UnitState.ISSUED, expires_in_days=10)

    response = api.post("/api/units/D-6/renew/", {"period_days": 30, "price_cents": 100}, format="json")

    unit.refresh_from_db()
    assert response.status_code == 200
    assert response.data["previous_expires_at"] is not None
    assert unit.renewals.count() == 1


@pytest.mark.django_db
def test_list_and_expiring_share_the_same_envelope(api, make_unit):
    """Third defect: neighbouring endpoints returned different shapes.

    `/units/` gave {count, next, previous, results} and `/units/expiring/`
    a bare list. The client would have to keep two parsing branches, and
    sooner or later one of them is forgotten.
    """
    make_unit("D-7", state=UnitState.ISSUED, expires_in_days=3)

    listed = api.get("/api/units/").data
    expiring = api.get("/api/units/expiring/?days=14").data

    assert set(listed) == set(expiring) == {"count", "next", "previous", "results"}
    assert [row["ref"] for row in expiring["results"]] == ["D-7"]


@pytest.mark.django_db
def test_expiring_rejects_garbage_days(api):
    assert api.get("/api/units/expiring/?days=abc").status_code == 400
    assert api.get("/api/units/expiring/?days=-5").status_code == 400


@pytest.mark.django_db
def test_healthz_reports_database(api):
    """Fourth defect: the heartbeat answered ok without touching the database.

    A load balancer would have kept the instance in rotation with a dead
    database behind it.
    """
    response = api.get("/healthz/")
    assert response.status_code == 200
    assert response.data == {"status": "ok", "database": "ok"}


@pytest.mark.django_db
def test_unknown_unit_returns_404_through_global_handler(api, client_rec):
    """try/except is gone from the views, so we check the handler replaced it."""
    response = api.post(
        "/api/issues/",
        {"unit_ref": "NOPE", "client_id": client_rec.id, "price_cents": 100},
        format="json",
    )
    assert response.status_code == 404
    assert response.data["code"] == "NotFound"
