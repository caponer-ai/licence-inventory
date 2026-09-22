from django.contrib import admin, messages
from django.db.models import QuerySet
from django.http import HttpRequest

from . import services
from .models import Client, Event, Issue, ReminderLog, Renewal, Unit, WarrantyClaim


@admin.register(Unit)
class UnitAdmin(admin.ModelAdmin):
    list_display = ("ref", "tier", "state", "expires_at", "cost_cents")
    list_filter = ("state", "tier")
    search_fields = ("ref", "note")
    # Стан і строк дії міняються тільки через сервіс: інакше адмінка
    # лишається дірою в тому самому інваріанті, який закритий в API.
    # Продовжити строк можна дією нижче, вона пише в Renewal і в журнал.
    readonly_fields = ("state", "expires_at")
    actions = ["renew_30_days"]

    @admin.action(description="Продовити на 30 днів")
    def renew_30_days(self, request: HttpRequest, queryset: QuerySet) -> None:
        actor = request.user.username or "admin"
        done = 0
        for unit in queryset:
            try:
                services.renew_unit(
                    unit_ref=unit.ref, period_days=30, price_cents=0, actor=actor
                )
                done += 1
            except services.DomainError as exc:
                self.message_user(request, f"{unit.ref}: {exc}", level=messages.WARNING)
        self.message_user(request, f"продовжено: {done}")


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("name", "contact", "created_at")
    search_fields = ("name", "contact")


@admin.register(Issue)
class IssueAdmin(admin.ModelAdmin):
    list_display = (
        "unit",
        "client",
        "issued_at",
        "warranty_until",
        "price_cents",
        "is_active",
    )
    list_filter = ("is_active",)
    search_fields = ("unit__ref", "client__name")


@admin.register(Renewal)
class RenewalAdmin(admin.ModelAdmin):
    list_display = (
        "unit",
        "period_days",
        "price_cents",
        "previous_expires_at",
        "new_expires_at",
    )


@admin.register(WarrantyClaim)
class WarrantyClaimAdmin(admin.ModelAdmin):
    list_display = ("issue", "state", "replacement_unit", "created_at", "resolved_at")
    list_filter = ("state",)


@admin.register(Event)
class EventAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "unit", "issue")
    list_filter = ("action",)
    search_fields = ("unit__ref", "actor")

    def has_add_permission(self, request: HttpRequest) -> bool:
        return False

    def has_change_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False

    def has_delete_permission(self, request: HttpRequest, obj: object = None) -> bool:
        return False


@admin.register(ReminderLog)
class ReminderLogAdmin(admin.ModelAdmin):
    list_display = ("unit", "kind", "for_expires_at", "created_at")
