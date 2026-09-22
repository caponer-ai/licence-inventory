"""Білий список переходів станів."""

import pytest

from inventory import services
from inventory.states import ALLOWED_TRANSITIONS, UnitState, can_move


def test_revoked_is_terminal():
    assert ALLOWED_TRANSITIONS[UnitState.REVOKED] == frozenset()


@pytest.mark.parametrize(
    "current,target,expected",
    [
        (UnitState.AVAILABLE, UnitState.ISSUED, True),
        (UnitState.ISSUED, UnitState.EXPIRED, True),
        (UnitState.EXPIRED, UnitState.ISSUED, True),
        (UnitState.ISSUED, UnitState.AVAILABLE, False),
        (UnitState.REVOKED, UnitState.AVAILABLE, False),
        (UnitState.EXPIRED, UnitState.AVAILABLE, False),
    ],
)
def test_transition_whitelist(current, target, expected):
    assert can_move(current, target) is expected


@pytest.mark.django_db
def test_illegal_transition_raises(make_unit):
    unit = make_unit("T-1", state=UnitState.ISSUED)
    with pytest.raises(services.IllegalTransition):
        services.move_state(unit, UnitState.AVAILABLE, actor="tester")


@pytest.mark.django_db
def test_same_state_is_noop_and_not_logged(make_unit):
    """Повторний виклик не має засмічувати журнал фальшивою подією."""
    from inventory.models import Event

    unit = make_unit("T-2", state=UnitState.AVAILABLE)
    services.move_state(unit, UnitState.AVAILABLE, actor="tester")
    assert Event.objects.count() == 0
