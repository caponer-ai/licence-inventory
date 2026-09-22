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
from inventory.serializers import UnitSerializer
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
    would have been nothing left to explain the new date to the client.

    The refusal is explicit rather than silent. Dropping the field quietly
    would leave the caller believing the change landed; a 400 tells them
    which action to use instead.
    """
    unit = make_unit("D-4", state=UnitState.ISSUED, expires_in_days=10)
    before = unit.expires_at

    response = api.put(
        "/api/units/D-4/",
        {"ref": "D-4", "tier": "individual", "cost_cents": 0, "expires_at": "2030-01-01T00:00:00Z"},
        format="json",
    )

    unit.refresh_from_db()
    assert response.status_code == 400
    assert "renew" in str(response.data)
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


@pytest.mark.django_db
def test_patch_does_not_roll_back_a_concurrent_renewal(api, make_unit):
    """read_only_fields blocks accepting a value, not writing one.

    The default ModelSerializer.update() saves every column from the object
    it loaded at the start of the request. A PATCH that only edits a note
    therefore overwrote an expiry that a renewal had committed a moment
    earlier: the Renewal row and the audit entry survived, the actual date
    rolled back, and nothing anywhere reported an error.
    """
    unit = make_unit("PT-1", state=UnitState.ISSUED, expires_in_days=10)

    stale = UnitSerializer(Unit.objects.get(ref="PT-1"), data={"note": "just a note"}, partial=True)
    stale.is_valid(raise_exception=True)

    services.renew_unit(unit_ref="PT-1", period_days=365, price_cents=100)
    renewed_to = Unit.objects.get(ref="PT-1").expires_at

    stale.save()

    unit.refresh_from_db()
    assert unit.expires_at == renewed_to, "the note update rolled the renewal back"
    assert unit.note == "just a note"


@pytest.mark.django_db
def test_unknown_state_filter_is_a_client_error(api, make_unit):
    """An empty list for a typo sends the caller debugging their data."""
    make_unit("FLT-1")
    assert api.get("/api/units/?state=avaliable").status_code == 400
    assert api.get("/api/units/?state=available").status_code == 200


@pytest.mark.django_db
def test_absurd_horizon_is_refused_before_it_overflows(api):
    """timedelta overflows above roughly 2.7 million days."""
    assert api.get("/api/units/expiring/?days=1000000000").status_code == 400
    assert api.get("/api/units/expiring/?days=3650").status_code == 200


@pytest.mark.django_db
def test_empty_unit_ref_filter_is_refused(api):
    assert api.get("/api/events/?unit_ref=").status_code == 400


@pytest.mark.django_db
def test_schema_documents_the_real_request_body(api):
    """The serializer built inside a method does not describe it by itself.

    Without explicit @extend_schema the schema advertised IssueSerializer
    for POST /api/issues/, which is the response shape, not the request.
    """
    import json

    schema = json.loads(api.get("/api/schema/?format=json").content)
    body = schema["paths"]["/api/issues/"]["post"]["requestBody"]
    ref = body["content"]["application/json"]["schema"]["$ref"]
    assert ref.endswith("IssueCreate"), ref


@pytest.mark.django_db
def test_expiry_can_be_set_when_registering_a_unit(api):
    """A licence bought with a known end date has to be enterable.

    Before this the only way to get an expiry was a renewal counted in whole
    days from now, which cannot express "this one ends on the 14th".
    """
    response = api.post(
        "/api/units/",
        {"ref": "REG-1", "tier": "company", "expires_at": "2030-01-01T00:00:00Z"},
        format="json",
    )

    assert response.status_code == 201
    assert Unit.objects.get(ref="REG-1").expires_at.year == 2030


@pytest.mark.django_db
def test_unroutable_ref_is_refused(api):
    """The ref is the URL lookup, so it has to be addressable.

    A unit named "expiring" would collide with the list action, and one with
    a dot or a slash could not be addressed at all.
    """
    for bad in ("has.dot", "has/slash", "expiring"):
        response = api.post("/api/units/", {"ref": bad}, format="json")
        assert response.status_code == 400, bad
    assert api.post("/api/units/", {"ref": "UNIT-042"}, format="json").status_code == 201


@pytest.mark.django_db
def test_reserved_refs_match_the_router(api):
    """A new list action must not silently become a valid unit ref.

    The reserved name lives in a validator on the model while the action
    lives on the viewset. This test is the thing that keeps the two from
    drifting apart: add an action, forget the validator, and it fails.
    """
    from inventory.views import UnitViewSet

    list_actions = [
        getattr(UnitViewSet, name).url_path
        for name in dir(UnitViewSet)
        if getattr(getattr(UnitViewSet, name, None), "detail", None) is False
    ]
    assert list_actions, "no list actions found; the check below would be vacuous"

    for name in list_actions:
        response = api.post("/api/units/", {"ref": name}, format="json")
        assert response.status_code == 400, f"{name} is routable but accepted as a ref"
