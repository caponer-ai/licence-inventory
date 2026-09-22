"""Access, permissions, and an audit log that names a person.

The security round started from a simple complaint: the README promised
"no state change without a trace", yet every entry carried the actor
`anonymous`. A log that cannot answer "who" only answers "something
happened". The tests below pin that this is fixed rather than declared.
"""

import pytest

from inventory import services
from inventory.auth_views import LoginThrottle
from inventory.models import Event


@pytest.mark.django_db
def test_anonymous_cannot_read_client_contacts(api_anon, client_rec):
    """The nastiest finding of the round: contacts were served without a token."""
    response = api_anon.get("/api/clients/")
    assert response.status_code == 401


@pytest.mark.django_db
def test_anonymous_cannot_issue(api_anon, make_unit, client_rec):
    make_unit("AUTH-1")
    response = api_anon.post(
        "/api/issues/",
        {"unit_ref": "AUTH-1", "client_id": client_rec.id, "price_cents": 100},
        format="json",
    )
    assert response.status_code == 401


@pytest.mark.django_db
def test_token_obtained_by_password_works(api_anon, user):
    user.set_password("secret-pass-123")
    user.save()

    granted = api_anon.post(
        "/api/auth/token/",
        {"username": user.username, "password": "secret-pass-123"},
        format="json",
    )
    assert granted.status_code == 200
    token = granted.data["token"]

    api_anon.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_anon.get("/api/units/").status_code == 200


@pytest.mark.django_db
def test_audit_log_records_the_real_username(api, make_unit, client_rec, user):
    """This is what the whole authentication layer is for.

    Before it, this assertion would read `anonymous` and the log would be
    pointless.
    """
    make_unit("AUTH-2")
    api.post(
        "/api/issues/",
        {"unit_ref": "AUTH-2", "client_id": client_rec.id, "price_cents": 100},
        format="json",
    )

    actors = set(Event.objects.values_list("actor", flat=True))
    assert actors == {user.username}


@pytest.mark.django_db
def test_ordinary_user_cannot_delete(api, make_unit):
    """Deletion is the one action history does not save, hence staff only."""
    make_unit("AUTH-3")
    assert api.delete("/api/units/AUTH-3/").status_code == 403


@pytest.mark.django_db
def test_ordinary_user_can_still_work(api, make_unit, client_rec):
    make_unit("AUTH-4")
    assert (
        api.post(
            "/api/issues/",
            {"unit_ref": "AUTH-4", "client_id": client_rec.id, "price_cents": 100},
            format="json",
        ).status_code
        == 201
    )


@pytest.mark.django_db
def test_healthz_stays_open_for_the_load_balancer(api_anon):
    """The load balancer has no token, and no data leaks from here."""
    assert api_anon.get("/healthz/").status_code == 200


@pytest.mark.django_db
def test_event_log_is_read_only_over_http(api, make_unit, client_rec):
    make_unit("AUTH-5")
    services.issue_unit(unit_ref="AUTH-5", client_id=client_rec.id, price_cents=100)

    assert api.get("/api/events/").status_code == 200
    assert api.post("/api/events/", {"action": "forged"}, format="json").status_code == 405


@pytest.mark.django_db
def test_login_is_rate_limited(api_anon, user):
    """Guessing a password has to cost time.

    DRF's ready-made `ObtainAuthToken` is declared with
    `throttle_classes = ()`, so its counter is off. For the one endpoint
    that checks a password, that is the worst possible place for "no
    limit", hence a custom view with its own scope.

    Order of checks in DRF: authentication, permissions, throttle. Because
    of that an anonymous request to a closed endpoint gets a 401 before the
    counter ever runs, so the limit has to be exercised on the open login.
    """
    LoginThrottle.cache.clear()
    codes = []
    for _ in range(8):
        response = api_anon.post(
            "/api/auth/token/",
            {"username": user.username, "password": "wrong"},
            format="json",
        )
        codes.append(response.status_code)
    LoginThrottle.cache.clear()

    assert 429 in codes, f"brute force is not limited, codes: {codes}"
    assert codes[0] == 400, "the first failed attempt should be an ordinary refusal"


@pytest.mark.django_db
def test_login_limit_does_not_block_normal_api_use(api, make_unit):
    """A strict login limit must not touch authenticated working requests."""
    LoginThrottle.cache.clear()
    make_unit("THR-1")
    codes = {api.get("/api/units/").status_code for _ in range(20)}
    assert codes == {200}


@pytest.mark.django_db
def test_openapi_schema_is_served(api):
    """A REST API for a site and mobile apps is consumed through its schema,
    so the schema has to live at a URL, not in the README."""
    response = api.get("/api/schema/")
    assert response.status_code == 200
    assert b"licence-inventory" in response.content


@pytest.mark.django_db
def test_swagger_ui_is_served(api):
    assert api.get("/api/docs/").status_code == 200
