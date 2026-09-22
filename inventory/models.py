"""Моделі інвентаря одиниць з обмеженим строком дії.

Гроші всюди в цілих копійках/центах (``*_cents``). Float для грошей не
використовуємо: 0.1 + 0.2 != 0.3, а на звірці з платіжкою це виїде.

Час усюди aware (USE_TZ=True). Наївний datetime у порівняннях строків
дає мовчазні зсуви на годину два рази на рік.
"""

from django.core.validators import MinValueValidator
from django.db import models

from .states import ClaimState, Tier, UnitState


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Client(TimeStamped):
    name = models.CharField(max_length=200)
    contact = models.CharField(
        max_length=200, blank=True, help_text="пошта або телеграм"
    )

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Unit(TimeStamped):
    """Одиниця інвентаря: ліцензія, акаунт, підписка.

    ``expires_at`` це строк дії самої одиниці, а не видачі. Видача може
    закінчитись раніше, строк дії лишається властивістю одиниці.
    """

    ref = models.CharField(
        max_length=64, unique=True, help_text="зовнішній ідентифікатор"
    )
    tier = models.CharField(
        max_length=16, choices=Tier.choices, default=Tier.INDIVIDUAL
    )
    state = models.CharField(
        max_length=16,
        choices=UnitState.choices,
        default=UnitState.AVAILABLE,
        db_index=True,
    )
    cost_cents = models.PositiveIntegerField(
        default=0, help_text="собівартість у центах", validators=[MinValueValidator(0)]
    )
    acquired_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["ref"]
        indexes = [models.Index(fields=["state", "expires_at"])]

    def __str__(self) -> str:
        return f"{self.ref} ({self.get_state_display()})"

    @property
    def active_issue(self) -> "Issue | None":
        return self.issues.filter(is_active=True).first()


class Issue(TimeStamped):
    """Факт видачі одиниці клієнту.

    Одна одиниця не може мати дві активні видачі одночасно. Це тримається
    не лише кодом сервісу, а й частковим унікальним індексом у БД: якщо
    два запити прослизнуть паралельно, впаде другий, а не зіпсуються дані.
    """

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="issues")
    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="issues")
    issued_at = models.DateTimeField(db_index=True)
    warranty_days = models.PositiveSmallIntegerField(default=7)
    warranty_until = models.DateTimeField(db_index=True)
    price_cents = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    is_active = models.BooleanField(default=True)
    closed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-issued_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["unit"],
                condition=models.Q(is_active=True),
                name="one_active_issue_per_unit",
            )
        ]

    def __str__(self) -> str:
        return f"{self.unit.ref} -> {self.client.name}"


class Renewal(TimeStamped):
    """Продовження строку дії одиниці.

    Зберігаємо і попередній, і новий строк: без цього неможливо
    пояснити клієнту, звідки взялась дата, і неможливо відкотити.
    """

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="renewals")
    period_days = models.PositiveSmallIntegerField()
    price_cents = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    previous_expires_at = models.DateTimeField(null=True, blank=True)
    new_expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.unit.ref} +{self.period_days}д до {self.new_expires_at:%Y-%m-%d}"


class WarrantyClaim(TimeStamped):
    """Рекламація в гарантійному вікні."""

    issue = models.ForeignKey(Issue, on_delete=models.PROTECT, related_name="claims")
    reason = models.TextField()
    state = models.CharField(
        max_length=16, choices=ClaimState.choices, default=ClaimState.OPEN
    )
    replacement_unit = models.ForeignKey(
        Unit,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="replacement_for",
    )
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            # Та сама логіка, що в сервісі, але на рівні БД: якщо два
            # запити прослизнуть паралельно, впаде другий, а не
            # зʼявиться друга безкоштовна заміна.
            models.UniqueConstraint(
                fields=["issue"],
                condition=models.Q(state="open"),
                name="one_open_claim_per_issue",
            )
        ]

    def __str__(self) -> str:
        return f"{self.issue.unit.ref}: {self.get_state_display()}"


class Event(models.Model):
    """Журнал дій. Пишеться на кожну зміну стану, ніколи не оновлюється.

    Навіщо: коли клієнт питає «чому акаунт відкликаний», відповідь має
    бути в системі, а не в чиїйсь пам'яті.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.CharField(max_length=100, default="system")
    action = models.CharField(max_length=64, db_index=True)
    unit = models.ForeignKey(
        Unit, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    issue = models.ForeignKey(
        Issue, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"


class ReminderLog(models.Model):
    """Слід відправленого нагадування.

    Ключ ідемпотентності: (одиниця, тип, строк, на який нагадували).
    Команду нагадувань можна запускати хоч щогодини, клієнт отримає
    один лист на один строк. Перезапуск cron не дублює розсилку.
    """

    unit = models.ForeignKey(Unit, on_delete=models.CASCADE, related_name="reminders")
    kind = models.CharField(max_length=32, default="renewal")
    for_expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["unit", "kind", "for_expires_at"],
                name="one_reminder_per_expiry",
            )
        ]

    def __str__(self) -> str:
        return f"{self.unit.ref} {self.kind} {self.for_expires_at:%Y-%m-%d}"
