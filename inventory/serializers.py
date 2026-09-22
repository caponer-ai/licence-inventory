from rest_framework import serializers

from .models import Client, Event, Issue, Renewal, Unit, WarrantyClaim


class ClientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Client
        fields = ["id", "name", "contact", "created_at"]


class UnitSerializer(serializers.ModelSerializer):
    state_display = serializers.CharField(source="get_state_display", read_only=True)

    class Meta:
        model = Unit
        fields = [
            "id",
            "ref",
            "tier",
            "state",
            "state_display",
            "cost_cents",
            "acquired_at",
            "expires_at",
            "note",
        ]
        # expires_at змінюється лише через renew, щоб кожна нова дата
        # мала запис у Renewal і подію в журналі.
        read_only_fields = ["state", "expires_at"]


class IssueSerializer(serializers.ModelSerializer):
    unit_ref = serializers.CharField(source="unit.ref", read_only=True)
    client_name = serializers.CharField(source="client.name", read_only=True)

    class Meta:
        model = Issue
        fields = [
            "id",
            "unit_ref",
            "client_name",
            "issued_at",
            "warranty_days",
            "warranty_until",
            "price_cents",
            "is_active",
            "closed_at",
        ]


class IssueCreateSerializer(serializers.Serializer):
    """Вхід окремо від виходу: клієнт не надсилає стан, ми не приймаємо зайве."""

    unit_ref = serializers.CharField(max_length=64)
    client_id = serializers.IntegerField(min_value=1)
    price_cents = serializers.IntegerField(min_value=0)
    warranty_days = serializers.IntegerField(min_value=0, max_value=365, default=7)


class RenewSerializer(serializers.Serializer):
    period_days = serializers.IntegerField(min_value=1, max_value=3650)
    price_cents = serializers.IntegerField(min_value=0)


class ClaimCreateSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=2000)


class ClaimApproveSerializer(serializers.Serializer):
    replacement_ref = serializers.CharField(max_length=64)


class RenewalSerializer(serializers.ModelSerializer):
    class Meta:
        model = Renewal
        fields = [
            "id",
            "unit",
            "period_days",
            "price_cents",
            "previous_expires_at",
            "new_expires_at",
            "created_at",
        ]


class WarrantyClaimSerializer(serializers.ModelSerializer):
    class Meta:
        model = WarrantyClaim
        fields = [
            "id",
            "issue",
            "reason",
            "state",
            "replacement_unit",
            "created_at",
            "resolved_at",
        ]


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = ["id", "created_at", "actor", "action", "unit", "issue", "payload"]
