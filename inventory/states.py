"""Unit states and the transitions allowed between them.

The transition table is kept separate from the models on purpose: the rule
"what can turn into what" reads in one glance and is covered by a test,
instead of being scattered across views as a pile of if-statements.
"""

from django.db import models


class UnitState(models.TextChoices):
    AVAILABLE = "available", "available"
    RESERVED = "reserved", "reserved"
    ISSUED = "issued", "issued"
    EXPIRED = "expired", "expired"
    REVOKED = "revoked", "revoked"


#: Allow-list of transitions. Anything not listed here is forbidden.
#: REVOKED is terminal: a revoked unit is never resurrected, a new one is
#: registered instead.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    UnitState.AVAILABLE: frozenset({UnitState.RESERVED, UnitState.ISSUED, UnitState.REVOKED}),
    UnitState.RESERVED: frozenset({UnitState.AVAILABLE, UnitState.ISSUED, UnitState.REVOKED}),
    UnitState.ISSUED: frozenset({UnitState.EXPIRED, UnitState.REVOKED}),
    UnitState.EXPIRED: frozenset({UnitState.ISSUED, UnitState.REVOKED}),
    UnitState.REVOKED: frozenset(),
}


def can_move(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


class ClaimState(models.TextChoices):
    OPEN = "open", "open"
    APPROVED = "approved", "approved"
    REJECTED = "rejected", "rejected"


class Tier(models.TextChoices):
    INDIVIDUAL = "individual", "individual"
    COMPANY = "company", "company"
