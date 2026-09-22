from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone
from rest_framework.authtoken.models import Token
from rest_framework.test import APIClient

from inventory.models import Client, Unit
from inventory.states import UnitState


@pytest.fixture
def user(db):
    return User.objects.create_user(username="olha", password="x")


@pytest.fixture
def staff(db):
    return User.objects.create_user(username="admin-kate", password="x", is_staff=True)


@pytest.fixture
def api(user):
    """Автентифікований клієнт: звичайний робочий випадок.

    Токен, а не force_login, щоб тести йшли тим самим шляхом, що й
    зовнішній клієнт: заголовок Authorization.
    """
    client = APIClient()
    token, _ = Token.objects.get_or_create(user=user)
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


@pytest.fixture
def api_staff(staff):
    client = APIClient()
    token, _ = Token.objects.get_or_create(user=staff)
    client.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")
    return client


@pytest.fixture
def api_anon():
    return APIClient()


@pytest.fixture
def client_rec(db):
    return Client.objects.create(name="ТОВ Ромашка", contact="@romashka")


@pytest.fixture
def make_unit(db):
    def _make(
        ref="U-001", *, state=UnitState.AVAILABLE, expires_in_days=None, **kwargs
    ):
        expires_at = (
            timezone.now() + timedelta(days=expires_in_days)
            if expires_in_days is not None
            else None
        )
        return Unit.objects.create(
            ref=ref, state=state, expires_at=expires_at, **kwargs
        )

    return _make
