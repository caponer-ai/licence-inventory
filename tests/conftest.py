from datetime import timedelta

import pytest
from django.utils import timezone
from rest_framework.test import APIClient

from inventory.models import Client, Unit
from inventory.states import UnitState


@pytest.fixture
def api():
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
