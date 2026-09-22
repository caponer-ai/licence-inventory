"""Доступ, права і журнал, який справді називає людину.

Другий раунд критики почався з простої претензії: README обіцяв «жодної
зміни стану повз журнал», але в кожному записі стояв actor `anonymous`.
Журнал, який не вміє відповісти «хто», відповідає лише «щось сталось».
Тести нижче фіксують, що це виправлено, а не просто задекларовано.
"""

import pytest

from inventory import services
from inventory.auth_views import LoginThrottle
from inventory.models import Event


@pytest.mark.django_db
def test_anonymous_cannot_read_client_contacts(api_anon, client_rec):
    """Найнеприємніше з другого раунду: контакти віддавались без токена."""
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
        "/api/auth/token/", {"username": user.username, "password": "secret-pass-123"}, format="json"
    )
    assert granted.status_code == 200
    token = granted.data["token"]

    api_anon.credentials(HTTP_AUTHORIZATION=f"Token {token}")
    assert api_anon.get("/api/units/").status_code == 200


@pytest.mark.django_db
def test_audit_log_records_the_real_username(api, make_unit, client_rec, user):
    """Ось заради чого вся автентифікація.

    До цього тут стояло б `anonymous` і журнал не мав би сенсу.
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
    """Видалення це єдина дія, яку не рятує історія, тому лише персонал."""
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
    """Балансувальник не має токена. Даних звідси не витікає."""
    assert api_anon.get("/healthz/").status_code == 200


@pytest.mark.django_db
def test_event_log_is_read_only_over_http(api, make_unit, client_rec):
    make_unit("AUTH-5")
    services.issue_unit(unit_ref="AUTH-5", client_id=client_rec.id, price_cents=100)

    assert api.get("/api/events/").status_code == 200
    assert api.post("/api/events/", {"action": "підробка"}, format="json").status_code == 405


@pytest.mark.django_db
def test_login_is_rate_limited(api_anon, user):
    """Перебір пароля має коштувати часу.

    Готова `ObtainAuthToken` у DRF оголошена з `throttle_classes = ()`,
    тобто лічильник на ній вимкнено. Для єдиного ендпоінта, де
    перевіряється пароль, це найгірше місце для «без обмежень», тому
    тут своя в'юха з окремим scope.

    Порядок перевірок у DRF: автентифікація, права, throttle. Через це
    анонім на закритому ендпоінті отримує 401 ще до лічильника, і
    перевіряти обмеження треба саме на відкритому логіні.
    """
    LoginThrottle.cache.clear()
    codes = []
    for _ in range(8):
        response = api_anon.post(
            "/api/auth/token/",
            {"username": user.username, "password": "невірний"},
            format="json",
        )
        codes.append(response.status_code)
    LoginThrottle.cache.clear()

    assert 429 in codes, f"перебір не обмежується, коди: {codes}"
    assert codes[0] == 400, "перша невдала спроба має бути звичайною відмовою"


@pytest.mark.django_db
def test_login_limit_does_not_block_normal_api_use(api, make_unit):
    """Жорсткий ліміт на логін не має чіпати робочі запити з токеном."""
    LoginThrottle.cache.clear()
    make_unit("THR-1")
    codes = {api.get("/api/units/").status_code for _ in range(20)}
    assert codes == {200}


@pytest.mark.django_db
def test_openapi_schema_is_served(api):
    """Вакансія просить REST API для сайту і мобільних. Схема це те,
    чим вони користуються, тому вона має бути не в README, а за адресою."""
    response = api.get("/api/schema/")
    assert response.status_code == 200
    assert b"licence-inventory" in response.content


@pytest.mark.django_db
def test_swagger_ui_is_served(api):
    assert api.get("/api/docs/").status_code == 200
