# procurement/serializer.py
from __future__ import annotations
from decimal import Decimal

from rest_framework import serializers

from .models import (
    AuditLog, Supplier, LPO, LPOItem, LPOAttachment,
    GoodsReceipt, GoodsReceiptItem,
)
from .services import next_lpo_number, next_supplier_code


# =========================
# Supplier
# =========================
class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = [
            "id", "supplier_code", "name", "email", "phone", "address",
            "rc_number", "tax_id", "contact_person", "is_active",
        ]
        read_only_fields = ["id", "supplier_code"]

    def validate(self, attrs):
        # Soft duplicate guard (portable across SQLite/Postgres)
        name = (attrs.get("name") or "").strip()
        phone = (attrs.get("phone") or "").strip()
        email = (attrs.get("email") or "").strip().lower()
        rc = (attrs.get("rc_number") or "").strip().upper()
        tin = (attrs.get("tax_id") or "").strip().upper()

        qs = Supplier.objects.all()
        if self.instance:
            qs = qs.exclude(pk=self.instance.pk)

        if name:
            if phone and qs.filter(name__iexact=name, phone=phone).exists():
                raise serializers.ValidationError("Supplier with same name & phone already exists.")
            if email and qs.filter(name__iexact=name, email__iexact=email).exists():
                raise serializers.ValidationError("Supplier with same name & email already exists.")
        if rc and qs.filter(rc_number__iexact=rc).exists():
            raise serializers.ValidationError("RC number already exists for another supplier.")
        if tin and qs.filter(tax_id__iexact=tin).exists():
            raise serializers.ValidationError("Tax ID already exists for another supplier.")
        return attrs

    def create(self, validated_data):
        validated_data["supplier_code"] = next_supplier_code()
        return super().create(validated_data)


# =========================
# LPO Items
# =========================
class LPOItemSerializer(serializers.ModelSerializer):
    description = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = LPOItem
        fields = ["id", "inventory_item", "description", "qty", "unit_price", "line_total"]
        read_only_fields = ["id", "line_total"]


# =========================
# LPO (header)
# =========================
class LPOSerializer(serializers.ModelSerializer):
    items = LPOItemSerializer(many=True)

    class Meta:
        model = LPO
        fields = [
            "id", "supplier", "lpo_number", "status",
            "currency", "delivery_address", "expected_delivery_date", "payment_terms",
            "subtotal", "tax_amount", "discount_amount", "grand_total",
            "created_by", "approved_by", "created_at", "updated_at", "submitted_at", "approved_at",
            "items",
        ]
        read_only_fields = [
            "id", "lpo_number", "status", "subtotal", "grand_total",
            "approved_by", "created_at", "updated_at", "submitted_at", "approved_at", "created_by",
        ]

    # --- validations ---
    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("At least one item is required.")
        for it in items:
            if Decimal(it.get("qty") or "0") <= 0:
                raise serializers.ValidationError("Item qty must be > 0.")
            if Decimal(it.get("unit_price") or "0") < 0:
                raise serializers.ValidationError("Item unit_price cannot be negative.")
        return items

    def validate(self, attrs):
        tax = Decimal(attrs.get("tax_amount") or "0")
        disc = Decimal(attrs.get("discount_amount") or "0")
        if tax < 0:
            raise serializers.ValidationError({"tax_amount": "Tax amount cannot be negative."})
        if disc < 0:
            raise serializers.ValidationError({"discount_amount": "Discount amount cannot be negative."})
        return attrs

    # --- persistence ---
    def create(self, validated):
        items_data = validated.pop("items", [])
        user = self.context["request"].user
        validated["created_by"] = user
        if not validated.get("lpo_number"):
            validated["lpo_number"] = next_lpo_number()
        lpo = LPO.objects.create(**validated)
        for i in items_data:
            LPOItem.objects.create(lpo=lpo, **i)
        lpo.recompute_totals()
        lpo.save(update_fields=["subtotal", "grand_total"])
        return lpo

    def update(self, instance, validated):
        if not instance.is_editable:
            raise serializers.ValidationError("LPO is no longer editable.")
        items_data = validated.pop("items", None)

        for k, v in validated.items():
            setattr(instance, k, v)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            for i in items_data:
                LPOItem.objects.create(lpo=instance, **i)

        instance.recompute_totals()
        instance.save(update_fields=["subtotal", "grand_total"])
        return instance


# =========================
# LPO Attachment
# =========================
class LPOAttachmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = LPOAttachment
        fields = ["id", "lpo", "file", "kind", "mime_type", "size_kb", "width", "height", "checksum", "uploaded_at"]
        read_only_fields = ["id", "mime_type", "size_kb", "width", "height", "checksum", "uploaded_at"]


# =========================
# GRN (Goods Receipt)
# =========================
class GRNItemIn(serializers.Serializer):
    lpo_item = serializers.PrimaryKeyRelatedField(queryset=LPOItem.objects.all())
    qty_received = serializers.DecimalField(max_digits=14, decimal_places=2)

    def validate_qty_received(self, v):
        if Decimal(v) <= 0:
            raise serializers.ValidationError("qty_received must be > 0.")
        return v

class GoodsReceiptSerializer(serializers.ModelSerializer):
    items = GRNItemIn(many=True)

    class Meta:
        model = GoodsReceipt
        fields = ["id", "lpo", "received_by", "received_at", "reference", "note", "items"]
        read_only_fields = ["id", "received_by", "received_at"]

    def validate(self, attrs):
        lpo: LPO = attrs["lpo"]
        allowed = {
            getattr(LPO, "STATUS_APPROVED", "approved"),
            getattr(LPO, "STATUS_PARTIAL", "partially_received"),
        }
        if lpo.status not in allowed:
            raise serializers.ValidationError("Only approved or partially received LPO can receive goods.")
        return attrs

    def create(self, validated):
        # Be robust to extra kwargs a view might pass (e.g., created_by)
        validated.pop("created_by", None)

        items = validated.pop("items", [])
        if not items:
            raise serializers.ValidationError({"items": "At least one receipt item is required."})

        user = self.context["request"].user
        validated["received_by"] = user

        from decimal import Decimal as D
        from django.db import transaction
        from django.db.models import F

        with transaction.atomic():
            grn = GoodsReceipt.objects.create(**validated)

            for it in items:
                # Lock the LPO item while computing remaining
                lpo_item = LPOItem.objects.select_for_update().get(pk=it["lpo_item"].pk)
                remaining = lpo_item.qty - lpo_item.total_received
                qty_received = D(it["qty_received"])
                if qty_received > remaining:
                    raise serializers.ValidationError(f"Qty exceeds remaining ({remaining}).")

                GoodsReceiptItem.objects.create(grn=grn, **it)

                inv = lpo_item.inventory_item
                if hasattr(inv, "increase_stock"):
                    inv.increase_stock(qty_received)
                elif hasattr(inv, "quantity"):
                    type(inv).objects.filter(pk=inv.pk).update(quantity=F("quantity") + qty_received)

            lpo = grn.lpo
            lpo.refresh_receive_status()
            lpo.save(update_fields=["status"])

            AuditLog.objects.create(actor=user, verb="received", lpo=lpo, grn=grn)
            return grn