"""Бізнес-логіка інвентаря.

В'юхи навмисно тонкі: вони перекладають HTTP у виклик функції звідси і
назад. Уся логіка тут, тому її можна покрити тестами без HTTP, викликати
з management-команди і з адмінки однаково.

Кожна функція, що змінює стан, робить це в одній транзакції і пише подію
в журнал. Немає шляху змінити стан повз журнал.
"""

from __future__ import annotations

from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from .models import Client, Event, Issue, ReminderLog, Renewal, Unit, WarrantyClaim
from .states import ClaimState, UnitState, can_move


class DomainError(Exception):
    """Порушення бізнес-правила. Очікуване, не баг."""


class IllegalTransition(DomainError):
    pass


class UnitNotAvailable(DomainError):
    pass


class WarrantyExpired(DomainError):
    pass


def log(action: str, *, actor: str, unit=None, issue=None, **payload) -> Event:
    return Event.objects.create(
        action=action, actor=actor, unit=unit, issue=issue, payload=payload
    )


def move_state(unit: Unit, target: str, *, actor: str, reason: str = "") -> Unit:
    """Єдина точка зміни стану одиниці."""
    if unit.state == target:
        return unit
    if not can_move(unit.state, target):
        raise IllegalTransition(f"{unit.state} -> {target} заборонений")
    previous = unit.state
    unit.state = target
    unit.save(update_fields=["state", "updated_at"])
    log(
        "unit.state_changed",
        actor=actor,
        unit=unit,
        **{"from": previous, "to": target, "reason": reason},
    )
    return unit


@transaction.atomic
def issue_unit(
    *,
    unit_ref: str,
    client_id: int,
    price_cents: int,
    warranty_days: int = 7,
    actor: str = "system",
) -> Issue:
    """Видати одиницю клієнту.

    ``select_for_update`` тримає рядок до кінця транзакції: два одночасні
    запити на одну одиницю не зможуть обидва побачити її вільною. Частковий
    унікальний індекс у БД це друга лінія на випадок, якщо хтось колись
    викличе логіку повз цю функцію.
    """
    unit = Unit.objects.select_for_update().get(ref=unit_ref)
    client = Client.objects.get(pk=client_id)

    if unit.state not in (UnitState.AVAILABLE, UnitState.RESERVED):
        raise UnitNotAvailable(f"одиниця {unit_ref} у стані {unit.state}")

    now = timezone.now()
    issue = Issue.objects.create(
        unit=unit,
        client=client,
        issued_at=now,
        warranty_days=warranty_days,
        warranty_until=now + timedelta(days=warranty_days),
        price_cents=price_cents,
    )
    move_state(unit, UnitState.ISSUED, actor=actor, reason="видача")
    log(
        "issue.created",
        actor=actor,
        unit=unit,
        issue=issue,
        client=client.name,
        price_cents=price_cents,
    )
    return issue


@transaction.atomic
def renew_unit(
    *, unit_ref: str, period_days: int, price_cents: int, actor: str = "system"
) -> Renewal:
    """Продовжити строк дії одиниці.

    Відлік іде від ``max(зараз, поточний строк)``, а не від ``зараз``.
    Інакше клієнт, який продовжив за тиждень до кінця, мовчки втрачає
    ці сім оплачених днів. Це найчастіша помилка в такій логіці, тому на
    неї є окремий тест.
    """
    unit = Unit.objects.select_for_update().get(ref=unit_ref)
    if unit.state == UnitState.REVOKED:
        raise IllegalTransition("відкликану одиницю не продовжуємо")

    now = timezone.now()
    base = unit.expires_at if unit.expires_at and unit.expires_at > now else now
    previous = unit.expires_at
    unit.expires_at = base + timedelta(days=period_days)
    unit.save(update_fields=["expires_at", "updated_at"])

    if unit.state == UnitState.EXPIRED:
        move_state(unit, UnitState.ISSUED, actor=actor, reason="продовження")

    renewal = Renewal.objects.create(
        unit=unit,
        period_days=period_days,
        price_cents=price_cents,
        previous_expires_at=previous,
        new_expires_at=unit.expires_at,
    )
    log(
        "unit.renewed",
        actor=actor,
        unit=unit,
        period_days=period_days,
        price_cents=price_cents,
        new_expires_at=unit.expires_at.isoformat(),
    )
    return renewal


@transaction.atomic
def open_claim(*, issue_id: int, reason: str, actor: str = "system") -> WarrantyClaim:
    """Прийняти рекламацію, якщо гарантійне вікно ще відкрите."""
    issue = Issue.objects.select_related("unit").get(pk=issue_id)
    now = timezone.now()
    if now > issue.warranty_until:
        raise WarrantyExpired(
            f"гарантія на видачу {issue_id} закінчилась {issue.warranty_until:%Y-%m-%d %H:%M}"
        )
    claim = WarrantyClaim.objects.create(issue=issue, reason=reason)
    log("claim.opened", actor=actor, unit=issue.unit, issue=issue, reason=reason)
    return claim


@transaction.atomic
def approve_claim(
    *, claim_id: int, replacement_ref: str, actor: str = "system"
) -> Issue:
    """Задовольнити рекламацію: стару одиницю відкликати, видати заміну.

    Гарантія на заміну рахується від дати заміни. Стара видача
    закривається, але не видаляється: історія має лишитись повною.
    """
    claim = WarrantyClaim.objects.select_related("issue__unit", "issue__client").get(
        pk=claim_id
    )
    if claim.state != ClaimState.OPEN:
        raise DomainError(f"рекламація {claim_id} вже {claim.state}")

    old_issue = claim.issue
    old_unit = old_issue.unit

    old_issue.is_active = False
    old_issue.closed_at = timezone.now()
    old_issue.save(update_fields=["is_active", "closed_at", "updated_at"])
    move_state(
        old_unit, UnitState.REVOKED, actor=actor, reason=f"рекламація {claim_id}"
    )

    new_issue = issue_unit(
        unit_ref=replacement_ref,
        client_id=old_issue.client_id,
        price_cents=0,
        warranty_days=old_issue.warranty_days,
        actor=actor,
    )

    claim.state = ClaimState.APPROVED
    claim.replacement_unit = new_issue.unit
    claim.resolved_at = timezone.now()
    claim.save(update_fields=["state", "replacement_unit", "resolved_at", "updated_at"])
    log(
        "claim.approved",
        actor=actor,
        unit=new_issue.unit,
        issue=new_issue,
        replaced=old_unit.ref,
    )
    return new_issue


@transaction.atomic
def reject_claim(*, claim_id: int, actor: str = "system") -> WarrantyClaim:
    claim = WarrantyClaim.objects.get(pk=claim_id)
    if claim.state != ClaimState.OPEN:
        raise DomainError(f"рекламація {claim_id} вже {claim.state}")
    claim.state = ClaimState.REJECTED
    claim.resolved_at = timezone.now()
    claim.save(update_fields=["state", "resolved_at", "updated_at"])
    log("claim.rejected", actor=actor, issue=claim.issue)
    return claim


#: Скільки днів після закінчення строку одиниця ще вважається живою.
#: Далі це мертвий інвентар: нагадувати про продовження ліцензії, що
#: протухла два роки тому, означає спамити людину, яка давно пішла.
DEFAULT_GRACE_DAYS = 30


def expiring_units(days: int, *, grace_days: int = DEFAULT_GRACE_DAYS):
    """Одиниці, у яких строк спливає протягом ``days`` днів.

    Вікно двостороннє. Верхня межа очевидна: ``зараз + days``. Нижня
    менш очевидна і важливіша: без неї в вибірку падає весь архів, бо
    умова «строк <= зараз + 14 днів» істинна і для 2019 року.

    Тільки видані і прострочені. Вільна одиниця нікому не видана,
    нагадувати нема кому.
    """
    now = timezone.now()
    return Unit.objects.filter(
        state__in=[UnitState.ISSUED, UnitState.EXPIRED],
        expires_at__isnull=False,
        expires_at__gte=now - timedelta(days=grace_days),
        expires_at__lte=now + timedelta(days=days),
    ).order_by("expires_at")


def stale_issued_units():
    """Видані одиниці, у яких строк уже вичерпано. Без блокування, для перегляду."""
    return Unit.objects.filter(
        state=UnitState.ISSUED, expires_at__isnull=False, expires_at__lte=timezone.now()
    ).order_by("expires_at")


@transaction.atomic
def sweep_expired(*, actor: str = "system") -> int:
    """Перевести видані одиниці з вичерпаним строком у EXPIRED.

    Єдине місце, де зміна стану йде повз ``move_state``, і це свідомо:
    підмітання зачіпає скільки завгодно рядків, а порядковий save плюс
    порядковий запис події дали б два запити на одиницю.

    Інваріант при цьому не порушується. Перехід ISSUED -> EXPIRED
    перевірений у білому списку (``test_transition_whitelist``), а події
    пишуться тим самим пакетом, у тій самій транзакції. Тест
    ``test_sweep_writes_one_event_per_unit`` стежить, щоб кількість подій
    збігалась з кількістю переведених.
    """
    now = timezone.now()
    stale = list(
        Unit.objects.select_for_update().filter(
            state=UnitState.ISSUED, expires_at__isnull=False, expires_at__lte=now
        )
    )
    if not stale:
        return 0

    Unit.objects.filter(pk__in=[u.pk for u in stale]).update(
        state=UnitState.EXPIRED, updated_at=now
    )
    Event.objects.bulk_create(
        [
            Event(
                action="unit.state_changed",
                actor=actor,
                unit=u,
                payload={
                    "from": UnitState.ISSUED,
                    "to": UnitState.EXPIRED,
                    "reason": "строк вичерпано",
                },
            )
            for u in stale
        ]
    )
    return len(stale)


@transaction.atomic
def send_renewal_reminders(
    *, days: int, grace_days: int = DEFAULT_GRACE_DAYS, actor: str = "system"
) -> list[Unit]:
    """Нагадати про продовження, рівно один раз на один строк.

    Ідемпотентність тримає ``ReminderLog`` з унікальним ключем
    (одиниця, тип, строк). Повторний запуск нічого не надішле, тому cron
    можна ставити частіше, ніж раз на добу, і не боятись дублів.

    Кількість запитів стала і не залежить від розміру вибірки. Наївна
    версія робила ``get_or_create`` плюс запис події на кожну одиницю,
    тобто близько п'яти запитів на штуку: на десяти тисячах ліцензій це
    десятки тисяч звернень до бази за один запуск cron.

    ``select_for_update`` серіалізує два cron-и, що стартували одночасно:
    без нього обидва прочитали б порожній ReminderLog і обидва відзвітували
    б про відправку, хоча запис у базі лишився б один.
    """
    units = list(expiring_units(days, grace_days=grace_days).select_for_update())
    if not units:
        return []

    already = set(
        ReminderLog.objects.filter(kind="renewal", unit__in=units).values_list(
            "unit_id", "for_expires_at"
        )
    )
    fresh = [u for u in units if (u.id, u.expires_at) not in already]
    if not fresh:
        return []

    ReminderLog.objects.bulk_create(
        [ReminderLog(unit=u, kind="renewal", for_expires_at=u.expires_at) for u in fresh]
    )
    Event.objects.bulk_create(
        [
            Event(
                action="reminder.sent",
                actor=actor,
                unit=u,
                payload={"expires_at": u.expires_at.isoformat()},
            )
            for u in fresh
        ]
    )
    return fresh
