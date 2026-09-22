"""Шляхи, якими клієнт отримує більше, ніж оплатив.

П'ятий раунд критики. Усі попередні 76 тестів були зелені, коли
знайшлось це: одна оплачена видача давала дві безкоштовні заміни.

Такі дірки не видно з покриття рядків, бо кожен рядок окремо працює
правильно. Видно їх лише тоді, коли пройти сценарій цілком і подивитись
на підсумок у грошах.
"""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.utils import timezone

from inventory import services
from inventory.models import Issue, Unit, WarrantyClaim
from inventory.states import ClaimState, UnitState


@pytest.mark.django_db
def test_closed_issue_cannot_be_claimed_again(make_unit, client_rec):
    """Головна знахідка раунду.

    Сценарій: видача, рекламація, схвалення. Стара одиниця стає REVOKED,
    видача закривається, клієнт отримує заміну. Далі, поки гарантійне
    вікно ще відкрите, по ТІЙ САМІЙ старій видачі відкривається друга
    рекламація. Перехід REVOKED -> REVOKED це no-op і мовчки проходить,
    тому сервіс видавав другу безкоштовну заміну.

    Підсумок у грошах був: одна оплачена видача, дві безкоштовні одиниці.
    """
    make_unit("F-ORIG")
    make_unit("F-R1")
    make_unit("F-R2")
    issue = services.issue_unit(unit_ref="F-ORIG", client_id=client_rec.id, price_cents=35000)

    first = services.open_claim(issue_id=issue.id, reason="перша")
    services.approve_claim(claim_id=first.id, replacement_ref="F-R1")

    with pytest.raises(services.IssueClosed):
        services.open_claim(issue_id=issue.id, reason="друга по тій самій видачі")

    free = Issue.objects.filter(client=client_rec, price_cents=0).count()
    assert free == 1, "безкоштовна заміна має бути рівно одна на одну оплату"
    assert Unit.objects.get(ref="F-R2").state == UnitState.AVAILABLE


@pytest.mark.django_db
def test_second_open_claim_is_refused(make_unit, client_rec):
    """Та сама діра, коротший шлях: дві заявки до першого схвалення."""
    make_unit("F-2")
    issue = services.issue_unit(unit_ref="F-2", client_id=client_rec.id, price_cents=100)
    services.open_claim(issue_id=issue.id, reason="перша")

    with pytest.raises(services.ClaimAlreadyOpen):
        services.open_claim(issue_id=issue.id, reason="друга")


@pytest.mark.django_db
def test_database_refuses_second_open_claim_past_the_service(make_unit, client_rec):
    """Друга лінія, як і для видач: перевірка живе ще й у БД."""
    make_unit("F-3")
    issue = services.issue_unit(unit_ref="F-3", client_id=client_rec.id, price_cents=100)
    WarrantyClaim.objects.create(issue=issue, reason="перша")

    with pytest.raises(IntegrityError), transaction.atomic():
        WarrantyClaim.objects.create(issue=issue, reason="друга повз сервіс")


@pytest.mark.django_db
def test_rejected_claim_allows_a_new_one(make_unit, client_rec):
    """Обмеження не має замикати клієнта назавжди після відмови."""
    make_unit("F-4")
    issue = services.issue_unit(unit_ref="F-4", client_id=client_rec.id, price_cents=100)
    first = services.open_claim(issue_id=issue.id, reason="перша")
    services.reject_claim(claim_id=first.id)

    second = services.open_claim(issue_id=issue.id, reason="нові обставини")

    assert second.state == ClaimState.OPEN


@pytest.mark.django_db
def test_expired_unit_cannot_be_sold(make_unit, client_rec):
    """Стан AVAILABLE каже «нікому не видана», а не «працює».

    Одиницю можна продовжити, поки вона вільна, потім вона полежить і
    протухне. Стан лишиться AVAILABLE. Без окремої перевірки клієнт
    платить і отримує мертву ліцензію.
    """
    Unit.objects.create(
        ref="F-DEAD", state=UnitState.AVAILABLE, expires_at=timezone.now() - timedelta(days=10)
    )

    with pytest.raises(services.UnitExpired):
        services.issue_unit(unit_ref="F-DEAD", client_id=client_rec.id, price_cents=35000)

    assert not Issue.objects.filter(unit__ref="F-DEAD").exists()


@pytest.mark.django_db
def test_unit_with_future_expiry_sells_normally(make_unit, client_rec):
    Unit.objects.create(ref="F-OK", state=UnitState.AVAILABLE, expires_at=timezone.now() + timedelta(days=30))
    issue = services.issue_unit(unit_ref="F-OK", client_id=client_rec.id, price_cents=100)
    assert issue.unit.state == UnitState.ISSUED


@pytest.mark.django_db
def test_unit_without_expiry_sells_normally(make_unit, client_rec):
    """Строк не заданий означає «безстрокова», а не «протухла»."""
    make_unit("F-NOEXP")
    issue = services.issue_unit(unit_ref="F-NOEXP", client_id=client_rec.id, price_cents=100)
    assert issue.unit.state == UnitState.ISSUED


@pytest.mark.django_db
def test_non_numeric_id_returns_404_not_500(api):
    """DRF пропускає в pk будь-що без слеша і крапки.

    `int("abc")` кидав ValueError, обробник його не знав, і клієнт
    отримував 500 замість 404.
    """
    assert api.post("/api/issues/abc/claim/", {"reason": "x"}, format="json").status_code == 404
    assert api.post("/api/claims/abc/approve/", {"replacement_ref": "z"}, format="json").status_code == 404
    assert api.post("/api/claims/abc/reject/", format="json").status_code == 404
