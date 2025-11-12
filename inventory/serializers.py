# inventory/serializers.py
from __future__ import annotations

from typing import Any
from rest_framework import serializers
from .models import InventoryAttachment, InventoryEntry, AuditLog


class NullableChoiceField(serializers.ChoiceField):
    """
    ChoiceField that gracefully treats '' and None as nulls.
    Useful for CharField(null=True, blank=True, choices=...).
    """
    def to_internal_value(self, data: Any):
        if data in ("", None):
            return None
        return super().to_internal_value(data)


# Try to read choices from the model field (preferred, single source of truth)
try:
    _payment_type_field = InventoryEntry._meta.get_field("payment_type")
    PAYMENT_TYPE_CHOICES = getattr(_payment_type_field, "choices", ()) or ()
except Exception:
    PAYMENT_TYPE_CHOICES = ()


class InventoryEntrySerializer(serializers.ModelSerializer):
    # ✅ Explicit nullable choice field fixes Redocly “nullable must have type” complaints
    payment_type = NullableChoiceField(
        choices=PAYMENT_TYPE_CHOICES,
        allow_null=True,
        required=False,
    )

    class Meta:
        model = InventoryEntry
        fields = "__all__"
        read_only_fields = (
            "id",
            "created_by",
            "modified_by",
            "created_at",
            "updated_at",
            "deleted",
        )

    def create(self, validated_data):
        # set creator; audit logging is done in the view
        user = self.context.get("request").user if self.context.get("request") else None
        if user and "created_by" not in validated_data:
            validated_data["created_by"] = user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        # set modifier; audit logging is done in the view
        user = self.context.get("request").user if self.context.get("request") else None
        if user:
            validated_data["modified_by"] = user
        return super().update(instance, validated_data)

    def validate(self, attrs):
        # Auto-compute net_weight if gross & tare provided and net not explicitly set
        gross = attrs.get("gross_weight")
        tare = attrs.get("tare_weight")
        net = attrs.get("net_weight")
        if net is None and gross is not None and tare is not None:
            attrs["net_weight"] = gross - tare

        # Non-negative numeric fields
        for f in ("quantity", "gross_weight", "tare_weight", "net_weight"):
            v = attrs.get(f)
            if v is not None and v < 0:
                raise serializers.ValidationError({f: "Must be ≥ 0"})
        return attrs


class InventoryAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryAttachment
        fields = [
            "id",
            "entry",
            "file",
            "kind",
            "mime_type",
            "size_kb",
            "width",
            "height",
            "checksum",
            "uploaded_by",
            "uploaded_at",
        ]
        read_only_fields = [
            "id",
            "mime_type",
            "size_kb",
            "width",
            "height",
            "checksum",
            "uploaded_by",
            "uploaded_at",
        ]


class AuditLogSerializer(serializers.ModelSerializer):
    entry_id = serializers.UUIDField(read_only=True)
    user_username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = AuditLog
        fields = ("id", "entry_id", "user_username", "action", "changes", "timestamp")
        read_only_fields = fields