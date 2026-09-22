from rest_framework import serializers

from .models import Client, Event, Issue, Unit, WarrantyClaim


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
        # expires_at changes only through `renew`, so that every new date
        # has a Renewal row and an entry in the audit log behind it.
        read_only_fields = ["state", "expires_at"]

    #: The fields a plain update is allowed to touch. Everything else about a
    #: unit changes through a domain operation.
    WRITABLE = ("ref", "tier", "cost_cents", "acquired_at", "note")

    def update(self, instance, validated_data):
        """Write only the writable fields, and only those.

        ``read_only_fields`` stops a value from being *accepted*, it does not
        stop it from being *written*. The default ModelSerializer.update()
        calls ``instance.save()`` with no ``update_fields``, so Django writes
        every column from the in-memory object, including the expiry it read
        at the start of the request.

        The consequence is a lost update with no error anywhere: a PATCH that
        only edits a note overwrites a renewal that committed a moment
        earlier. The Renewal row and the audit entry survive, the actual
        expiry silently rolls back, and the history starts lying.
        Covered by `test_patch_does_not_roll_back_a_concurrent_renewal`.
        """
        for field, value in validated_data.items():
            setattr(instance, field, value)
        touched = [f for f in self.WRITABLE if f in validated_data]
        instance.save(update_fields=[*touched, "updated_at"] if touched else None)
        return instance


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
    """Input kept separate from output: the client does not send state and
    we do not accept fields we did not ask for."""

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


class WarrantyClaimSerializer(serializers.ModelSerializer):
    class Meta:
        model = WarrantyClaim
        fields = ["id", "issue", "reason", "state", "replacement_unit", "created_at", "resolved_at"]


class EventSerializer(serializers.ModelSerializer):
    class Meta:
        model = Event
        fields = ["id", "created_at", "actor", "action", "unit", "issue", "payload"]
