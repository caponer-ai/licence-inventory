"""HTTP-шар: коди відповідей і форма даних."""

import pytest

from inventory import services
from inventory.models import Unit
from inventory.states import UnitState


@pytest.mark.django_db
def test_healthz(api):
    assert api.get("/healthz/").status_code == 200


@pytest.mark.django_db
def test_issue_endpoint_creates_issue(api, make_unit, client_rec):
    make_unit("A-1")
    response = api.post(
        "/api/issues/",
        {"unit_ref": "A-1", "client_id": client_rec.id, "price_cents": 35000},
        format="json",
    )
    assert response.status_code == 201
    assert response.data["unit_ref"] == "A-1"
    assert Unit.objects.get(ref="A-1").state == UnitState.ISSUED


@pytest.mark.django_db
def test_double_issue_returns_409_not_500(api, make_unit, client_rec):
    """Порушене бізнес-правило це конфлікт, а не поломка сервера.

    500 сказав би клієнтському коду «спробуй ще раз», і він би довбав
    ендпоінт. 409 однозначно каже «стан не той», і його видно в моніторингу
    окремо від справжніх аварій.
    """
    make_unit("A-2")
    payload = {"unit_ref": "A-2", "client_id": client_rec.id, "price_cents": 100}
    api.post("/api/issues/", payload, format="json")

    response = api.post("/api/issues/", payload, format="json")

    assert response.status_code == 409
    assert response.data["code"] == "UnitNotAvailable"


@pytest.mark.django_db
def test_issue_unknown_unit_returns_404(api, client_rec):
    response = api.post(
        "/api/issues/",
        {"unit_ref": "NOPE", "client_id": client_rec.id, "price_cents": 100},
        format="json",
    )
    assert response.status_code == 404


@pytest.mark.django_db
def test_negative_price_rejected_by_validation(api, make_unit, client_rec):
    make_unit("A-3")
    response = api.post(
        "/api/issues/",
        {"unit_ref": "A-3", "client_id": client_rec.id, "price_cents": -1},
        format="json",
    )
    assert response.status_code == 400


@pytest.mark.django_db
def test_renew_endpoint(api, make_unit):
    make_unit("A-4", state=UnitState.ISSUED, expires_in_days=10)
    response = api.post(
        "/api/units/A-4/renew/",
        {"period_days": 30, "price_cents": 20000},
        format="json",
    )
    assert response.status_code == 200
    assert response.data["new_expires_at"] is not None


@pytest.mark.django_db
def test_expiring_endpoint_filters_by_horizon(api, make_unit):
    make_unit("A-5", state=UnitState.ISSUED, expires_in_days=3)
    make_unit("A-6", state=UnitState.ISSUED, expires_in_days=90)

    refs = [row["ref"] for row in api.get("/api/units/expiring/?days=14").data["results"]]

    assert refs == ["A-5"]


@pytest.mark.django_db
def test_units_filtered_by_state(api, make_unit):
    make_unit("A-7", state=UnitState.AVAILABLE)
    make_unit("A-8", state=UnitState.REVOKED)

    response = api.get("/api/units/?state=available")

    assert [row["ref"] for row in response.data["results"]] == ["A-7"]


@pytest.mark.django_db
def test_event_log_readable_per_unit(api, make_unit, client_rec):
    make_unit("A-9")
    services.issue_unit(unit_ref="A-9", client_id=client_rec.id, price_cents=100)

    response = api.get("/api/events/?unit_ref=A-9")

    actions = [row["action"] for row in response.data["results"]]
    assert "issue.created" in actions


@pytest.mark.django_db
def test_claim_flow_over_http(api, make_unit, client_rec):
    make_unit("A-10")
    make_unit("A-10-R")
    issue = services.issue_unit(
        unit_ref="A-10", client_id=client_rec.id, price_cents=100
    )

    claim = api.post(f"/api/issues/{issue.id}/claim/", {"reason": "бан"}, format="json")
    assert claim.status_code == 201

    approved = api.post(
        f"/api/claims/{claim.data['id']}/approve/",
        {"replacement_ref": "A-10-R"},
        format="json",
    )
    assert approved.status_code == 200
    assert approved.data["unit_ref"] == "A-10-R"
    assert Unit.objects.get(ref="A-10").state == UnitState.REVOKED
