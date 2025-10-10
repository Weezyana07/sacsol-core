# inventory/serializers.py
from rest_framework import serializers
from .models import InventoryEntry, AuditLog

class InventoryEntrySerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryEntry
        fields = "__all__"
        read_only_fields = ("id","created_by","modified_by","created_at","updated_at","deleted")

    def create(self, validated):
        # Do NOT log here (viewset will do it) to avoid double logs.
        user = self.context["request"].user if "request" in self.context else None
        validated["created_by"] = user
        return super().create(validated)

    def update(self, instance, validated):
        # Do NOT log here (viewset will do it).
        user = self.context["request"].user if "request" in self.context else None
        validated["modified_by"] = user
        return super().update(instance, validated)

    def validate(self, attrs):
        gross = attrs.get("gross_weight")
        tare  = attrs.get("tare_weight")
        net   = attrs.get("net_weight")
        if net is None and gross is not None and tare is not None:
            attrs["net_weight"] = gross - tare
        for f in ("quantity","gross_weight","tare_weight","net_weight"):
            v = attrs.get(f)
            if v is not None and v < 0:
                raise serializers.ValidationError({f: "Must be ≥ 0"})
        return attrs
    
class AuditLogSerializer(serializers.ModelSerializer):
    entry_id = serializers.UUIDField(read_only=True) 
    user_username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = AuditLog
        fields = ("id", "entry_id", "user_username", "action", "changes", "timestamp")
        read_only_fields = fields