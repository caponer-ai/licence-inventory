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
    """An authenticated client: the ordinary working case.

    A token rather than force_login, so the tests travel the same path an
    external client does: the Authorization header.
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
    return Client.objects.create(name="Romashka Ltd", contact="@romashka")


@pytest.fixture
def make_unit(db):
    def _make(ref="U-001", *, state=UnitState.AVAILABLE, expires_in_days=None, **kwargs):
        expires_at = timezone.now() + timedelta(days=expires_in_days) if expires_in_days is not None else None
        return Unit.objects.create(ref=ref, state=state, expires_at=expires_at, **kwargs)

    return _make


# Django hashes passwords with PBKDF2 and hundreds of thousands of
# iterations. That is correct in production and ruinous in tests: creating
# a user cost about 0.6 s, which on the full suite meant 22 s instead of 3 s.
# Hash strength is not what these tests check, so they use the cheapest hasher.
@pytest.fixture(autouse=True)
def fast_password_hashing(settings):
    settings.PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]


@pytest.fixture(autouse=True)
def clean_throttle_cache():
    """Rate limit counters live in the process and outlive a test.

    Without clearing, tests share one bucket keyed on 127.0.0.1: run order
    starts to affect the result, and one day that fails for no visible reason.
    """
    from django.core.cache import cache

    cache.clear()
    yield
    cache.clear()
