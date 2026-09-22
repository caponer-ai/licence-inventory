"""Стани одиниці інвентаря і дозволені переходи між ними.

Тримаємо переходи окремо від моделей навмисно: правило «що в що можна
перевести» читається одним поглядом і покривається тестом, а не розсіяне
по в'юхах у вигляді if-ів.
"""

from django.db import models


class UnitState(models.TextChoices):
    AVAILABLE = "available", "вільна"
    RESERVED = "reserved", "зарезервована"
    ISSUED = "issued", "видана"
    EXPIRED = "expired", "строк вийшов"
    REVOKED = "revoked", "відкликана"


#: Білий список переходів. Усе, чого тут немає, заборонено.
#: REVOKED термінальний: відкликану одиницю не воскрешаємо, заводимо нову.
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    UnitState.AVAILABLE: frozenset(
        {UnitState.RESERVED, UnitState.ISSUED, UnitState.REVOKED}
    ),
    UnitState.RESERVED: frozenset(
        {UnitState.AVAILABLE, UnitState.ISSUED, UnitState.REVOKED}
    ),
    UnitState.ISSUED: frozenset({UnitState.EXPIRED, UnitState.REVOKED}),
    UnitState.EXPIRED: frozenset({UnitState.ISSUED, UnitState.REVOKED}),
    UnitState.REVOKED: frozenset(),
}


def can_move(current: str, target: str) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


class ClaimState(models.TextChoices):
    OPEN = "open", "відкрита"
    APPROVED = "approved", "задоволена"
    REJECTED = "rejected", "відхилена"


class Tier(models.TextChoices):
    INDIVIDUAL = "individual", "індивідуальна"
    COMPANY = "company", "організаційна"
