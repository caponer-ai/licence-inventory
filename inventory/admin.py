from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from . import services
from .models import Client, Event, Issue, ReminderLog, Renewal, Unit, WarrantyClaim


class HistoryOnly(admin.ModelAdmin):
    """Records that describe what happened, not what should happen next.

    The admin is a normal application interface, not a DBA's SQL console, so
    it has to hold the same invariants the API holds. Left editable, it let
    anyone reopen a closed issue, move a claim to approved without issuing a
    replacement, rewrite a renewal, or delete a reminder log and make the
    same reminder fire twice.

    Domain changes go through the service layer, which writes history. These
    screens only show it.
    """

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("ref", "tier", "state", "expires_at", "cost_cents")
    list_filter = ("state", "tier")
    search_fields = ("ref", "note")
    # State and expiry change only through the service layer, otherwise the
    # admin stays a hole in the very invariant the API closes. Extending a
    # term is available as the action below, which writes a Renewal row and
    # an audit log entry.
    readonly_fields = ("state", "expires_at")
    actions = ["renew_30_days"]

    #: Mirrors the serializer: the only columns a plain edit may write.
    WRITABLE = ("ref", "tier", "cost_cents", "acquired_at", "note")

    def save_model(self, request: HttpRequest, obj: Unit, form: object, change: bool) -> None:
        """Save the edited columns, not every column on the object.

        `readonly_fields` removes a field from the *form*. It does nothing to
        the save: Django's default `save_model` calls `obj.save()` with no
        `update_fields`, so every column goes back to the database, including
        the expiry the admin page read when it was opened.

        A renewal that committed while the page was open was therefore
        rolled back by someone editing a note, with the Renewal row and the
        audit entry left behind to contradict the data.
        Covered by `test_admin_edit_does_not_roll_back_a_renewal`.
        """
        if not change:
            obj.save()
            return
        obj.save(update_fields=[*self.WRITABLE, "updated_at"])

    @admin.action(description="Renew for 30 days")
    def renew_30_days(self, request: HttpRequest, queryset: QuerySet) -> None:
        actor = request.user.username or "admin"
        done = 0
        for unit in queryset:
            try:
                services.renew_unit(unit_ref=unit.ref, period_days=30, price_cents=0, actor=actor)
                done += 1
            except services.DomainError as exc:
                self.message_user(request, f"{unit.ref}: {exc}", level=messages.WARNING)
        self.message_user(request, f"renewed: {done}")


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "contact", "created_at")
    search_fields = ("name", "contact")


@admin.register(Issue)
class IssueAdmin(HistoryOnly):
    list_display = ("unit", "client", "issued_at", "warranty_until", "price_cents", "is_active")
    list_filter = ("is_active",)
    search_fields = ("unit__ref", "client__name")


@admin.register(Renewal)
class RenewalAdmin(HistoryOnly):
    list_display = ("unit", "period_days", "price_cents", "previous_expires_at", "new_expires_at")


@admin.register(WarrantyClaim)
class WarrantyClaimAdmin(HistoryOnly):
    list_display = ("issue", "state", "replacement_unit", "created_at", "resolved_at")
    list_filter = ("state",)


@admin.register(Event)
class EventAdmin(HistoryOnly):
    list_display = ("created_at", "actor", "action", "unit", "issue")
    list_filter = ("action",)
    search_fields = ("unit__ref", "actor")


@admin.register(ReminderLog)
class ReminderLogAdmin(HistoryOnly):
    list_display = ("unit", "kind", "for_expires_at", "created_at")
