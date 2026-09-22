"""Models for an inventory of units with a limited lifetime.

Money is stored in whole cents everywhere (``*_cents``). Floats are not used
for money: 0.1 + 0.2 != 0.3, and that surfaces exactly when reconciling
against a payment provider.

All datetimes are timezone aware (USE_TZ=True). A naive datetime compared
against an expiry gives a silent one-hour shift twice a year.
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
    contact = models.CharField(max_length=200, blank=True, help_text="email or messenger handle")

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name


class Unit(TimeStamped):
    """An inventory unit: a licence, an account, a subscription.

    ``expires_at`` is the lifetime of the unit itself, not of an issue. An
    issue may end earlier; the expiry stays a property of the unit.
    """

    ref = models.CharField(max_length=64, unique=True, help_text="external identifier")
    tier = models.CharField(max_length=16, choices=Tier.choices, default=Tier.INDIVIDUAL)
    state = models.CharField(
        max_length=16,
        choices=UnitState.choices,
        default=UnitState.AVAILABLE,
        db_index=True,
    )
    cost_cents = models.PositiveIntegerField(
        default=0, help_text="acquisition cost in cents", validators=[MinValueValidator(0)]
    )
    acquired_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True, db_index=True)
    note = models.TextField(blank=True)

    class Meta:
        ordering = ["ref"]
        indexes = [models.Index(fields=["state", "expires_at"])]

    def __str__(self) -> str:
        return f"{self.ref} ({self.get_state_display()})"


class Issue(TimeStamped):
    """The fact of a unit being issued to a client.

    One unit cannot have two active issues at the same time. That is held
    not only by the service layer but by a partial unique index in the
    database: if two requests slip through in parallel, the second one
    fails instead of corrupting the data.
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
    """An extension of a unit's lifetime.

    Both the previous and the new expiry are stored. Without them it is
    impossible to explain to a client where the new date came from, and
    impossible to roll the change back.
    """

    unit = models.ForeignKey(Unit, on_delete=models.PROTECT, related_name="renewals")
    period_days = models.PositiveSmallIntegerField()
    price_cents = models.PositiveIntegerField(validators=[MinValueValidator(0)])
    previous_expires_at = models.DateTimeField(null=True, blank=True)
    new_expires_at = models.DateTimeField()

    class Meta:
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.unit.ref} +{self.period_days}d to {self.new_expires_at:%Y-%m-%d}"


class WarrantyClaim(TimeStamped):
    """A claim filed inside the warranty window."""

    issue = models.ForeignKey(Issue, on_delete=models.PROTECT, related_name="claims")
    reason = models.TextField()
    state = models.CharField(max_length=16, choices=ClaimState.choices, default=ClaimState.OPEN)
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
            # The same rule the service enforces, but at the database level:
            # if two requests slip through in parallel, the second one fails
            # instead of producing a second free replacement.
            models.UniqueConstraint(
                fields=["issue"],
                condition=models.Q(state="open"),
                name="one_open_claim_per_issue",
            )
        ]

    def __str__(self) -> str:
        return f"{self.issue.unit.ref}: {self.get_state_display()}"


class Event(models.Model):
    """The audit log. Written on every state change, never updated.

    Why it exists: when a client asks "why was my account revoked", the
    answer has to live in the system, not in somebody's memory.
    """

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    actor = models.CharField(max_length=100, default="system")
    action = models.CharField(max_length=64, db_index=True)
    unit = models.ForeignKey(Unit, on_delete=models.SET_NULL, null=True, blank=True, related_name="events")
    issue = models.ForeignKey(Issue, on_delete=models.SET_NULL, null=True, blank=True, related_name="events")
    payload = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"


class ReminderLog(models.Model):
    """A trace of a reminder that was sent.

    Idempotency key: (unit, kind, the expiry the reminder was about). The
    reminder command can run every hour and the client still receives one
    message per expiry date. Restarting cron never duplicates a send.
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
