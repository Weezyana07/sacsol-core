# procurement/serializers.py
from __future__ import annotations
from decimal import Decimal

from rest_framework import serializers

from inventory.models import InventoryEntry
from .models import (
    AuditLog, Supplier, LPO, LPOItem, LPOAttachment,
    GoodsReceipt, GoodsReceiptItem,
)
from drf_spectacular.utils import extend_schema_field, OpenApiTypes
from core.roles import is_manager_or_owner  

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
        from .services import next_supplier_code
        validated_data["supplier_code"] = next_supplier_code()
        return super().create(validated_data)


# =========================
# LPO Items
# =========================
from decimal import Decimal as D
from rest_framework import serializers
# ...imports...

class LPOItemSerializer(serializers.ModelSerializer):
    inventory_item = serializers.PrimaryKeyRelatedField(
        queryset=InventoryEntry.objects.all(),
        required=False,
        allow_null=True,
    )
    description = serializers.CharField(required=False, allow_blank=True)

    # READ-ONLY computed fields
    total_received = serializers.DecimalField(max_digits=14, decimal_places=2, read_only=True)  # <-- NO source
    received_so_far = serializers.SerializerMethodField(read_only=True)
    remaining = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = LPOItem
        fields = [
            "id","inventory_item", "description","qty","unit_price","line_total",
            "total_received",  "received_so_far",  "remaining",          
        ]
        read_only_fields = ["id", "line_total", "total_received", "received_so_far", "remaining"]

    def to_internal_value(self, data):
        d = dict(data)
        if d.get("inventory_item") in ("", None):
            d["inventory_item"] = None
        return super().to_internal_value(d)

    def validate(self, attrs):
        inv = attrs.get("inventory_item")
        desc = (attrs.get("description") or "").strip()
        if not inv and not desc:
            raise serializers.ValidationError("Provide either inventory_item or description.")
        if inv and not desc:
            desc = getattr(inv, "description", "") or getattr(inv, "mineral_or_equipment", "") or ""
            attrs["description"] = desc
        return attrs

    # ---------- computed helpers ----------
    def get_received_so_far(self, obj) -> D:
        # uses LPOItem.total_received @property
        return obj.total_received or D("0")

    def get_remaining(self, obj) -> D:
        rec = obj.total_received or D("0")
        return (obj.qty or D("0")) - rec

# =========================
# LPO (header)
# =========================
class LPOSerializer(serializers.ModelSerializer):
    # Make supplier optional so we can accept supplier_name
    supplier = serializers.PrimaryKeyRelatedField(
        queryset=Supplier.objects.all(),
        required=False,
        allow_null=True,
    )
    items = LPOItemSerializer(many=True)
    # allow typing the supplier name; we resolve/create in create/update
    supplier_name = serializers.CharField(write_only=True, required=False, allow_blank=True)
    supplier_name_display = serializers.CharField(source="supplier.name", read_only=True)

    created_by_name = serializers.SerializerMethodField(read_only=True)
    submitted_by = serializers.PrimaryKeyRelatedField(read_only=True)                 # ← NEW
    submitted_by_name = serializers.SerializerMethodField(read_only=True)             

    tax_enabled = serializers.BooleanField(default=True, required=False)
    tax_rate = serializers.DecimalField(max_digits=5, decimal_places=2, required=False, allow_null=True)
    tax_amount = serializers.DecimalField(max_digits=12, decimal_places=2, required=False)

    can_submit = serializers.SerializerMethodField(read_only=True)
    can_approve = serializers.SerializerMethodField(read_only=True)
    can_cancel = serializers.SerializerMethodField(read_only=True)
    can_receive = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = LPO
        fields = [
            "id", "supplier", "supplier_name", "supplier_name_display",
            "lpo_number", "status",
            "currency", "delivery_address", "expected_delivery_date", "payment_terms",
            "subtotal",
            # ↓ include these two
            "tax_enabled", "tax_rate",
            "tax_amount", "discount_amount", "grand_total",
            "created_by", "approved_by", "created_at", "updated_at", "submitted_at", "approved_at",
            "items", "created_by_name", "deleted", "submitted_by","submitted_by_name",
            "can_submit", "can_approve", "can_cancel", "can_receive", 
        ]
        read_only_fields = [
            "id", "lpo_number", "status", "subtotal", "grand_total",
            "approved_by", "created_at", "updated_at", "submitted_at", "approved_at", "created_by",
            "supplier_name_display", "submitted_by","submitted_by_name",
            "can_submit", "can_approve", "can_cancel", "can_receive",
        ]

    def _u(self):
        return getattr(self.context.get("request"), "user", None)

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_can_submit(self, obj: LPO):
        u = self._u()
        if not u or not u.is_authenticated: return False
        # Allow the creator (or superuser) to submit while draft and valid totals
        return (obj.status == obj.STATUS_DRAFT) and (obj.grand_total > 0) and (u == obj.created_by or u.is_superuser)

    # @extend_schema_field(OpenApiTypes.BOOL)
    # def get_can_approve(self, obj: LPO):
    #     u = self._u()
    #     if not u or not u.is_authenticated: return False
    #     # Only superusers (or users with a specific permission) can approve
    #     return (obj.status == obj.STATUS_SUBMITTED) and (
    #         u.is_superuser or u.has_perm("procurement.approve_lpo")
    #     )
    @extend_schema_field(OpenApiTypes.BOOL)
    def get_can_approve(self, obj: LPO):
        u = self._u()
        if not u or not u.is_authenticated:
            return False
        return (obj.status == obj.STATUS_SUBMITTED) and (u.is_superuser or is_manager_or_owner(u))

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_can_cancel(self, obj: LPO):
        u = self._u()
        if not u or not u.is_authenticated:
            return False
        if obj.status in {obj.STATUS_CANCELLED, obj.STATUS_FULFILLED}:
            return False
        # Super admin always allowed to cancel
        if getattr(u, "is_superuser", False):
            return True
        # Managers must NOT be able to cancel (even if they created it)
        if u.has_perm("procurement.approve_lpo"):
            return False
        # Everyone else cannot cancel
        return (obj.status == obj.STATUS_DRAFT) and (u == obj.created_by)

    @extend_schema_field(OpenApiTypes.BOOL)
    def get_can_receive(self, obj: LPO):
        u = self._u()
        if not u or not u.is_authenticated:
            return False
        return obj.status in {getattr(obj, "STATUS_APPROVED", "approved"),
                              getattr(obj, "STATUS_PARTIAL", "partially_received")}

    # --- validations ---
    @extend_schema_field(OpenApiTypes.STR)
    def get_submitted_by_name(self, obj):
        u = getattr(obj, "submitted_by", None)
        if not u:
            return None
        full = getattr(u, "get_full_name", lambda: "")()
        return full or getattr(u, "username", None) or getattr(u, "email", None)
    
    @extend_schema_field(OpenApiTypes.STR)
    def get_created_by_name(self, obj):
        u = getattr(obj, "created_by", None)
        if not u:
            return None
        full = getattr(u, "get_full_name", lambda: "")() or ""
        return full or getattr(u, "username", None)
    
    def validate_items(self, items):
        if not items:
            raise serializers.ValidationError("At least one item is required.")
        for it in items:
            if Decimal(it.get("qty") or "0") <= 0:
                raise serializers.ValidationError("Item qty must be > 0.")
            if Decimal(it.get("unit_price") or "0") < 0:
                raise serializers.ValidationError("Item unit_price cannot be negative.")
        return items
    
    # --- helpers ---
    def _ensure_supplier(self, data: dict) -> None:
        
        """
        Resolve supplier via one of:
          - explicit supplier id/instance in payload, or
          - supplier_name (case-insensitive match by name, otherwise create).
        Mutates `data` to set `supplier` to a **Supplier instance** and removes `supplier_name`.
        """
        supplied = data.get("supplier")
        if supplied:
            # DRF typically gives an instance here, but normalize if an int slips through
            if isinstance(supplied, int):
                try:
                    supplied = Supplier.objects.get(pk=supplied)
                except Supplier.DoesNotExist:
                    raise serializers.ValidationError({"supplier": "Supplier id does not exist."})
            data["supplier"] = supplied
            data.pop("supplier_name", None)
            return

        name = (data.pop("supplier_name", "") or "").strip()
        if not name:
            raise serializers.ValidationError({"supplier": "Supplier is required (id or supplier_name)."})
        
        obj = Supplier.objects.filter(name__iexact=name).first()
        if not obj:
            from .services import next_supplier_code
            obj = Supplier(name=name, supplier_code=next_supplier_code(), is_active=True)
            obj.save()
        # IMPORTANT: assign the instance (not pk)
        data["supplier"] = obj

    # --- persistence ---
    def validate(self, attrs):
        items = attrs.get("items") or []
        subtotal = sum(Decimal(i["qty"]) * Decimal(i["unit_price"]) for i in items)
        tax_enabled = attrs.get("tax_enabled", True)
        rate = attrs.get("tax_rate")
        if tax_enabled and rate is not None:
            attrs["tax_amount"] = (subtotal * (Decimal(rate) / Decimal("100"))).quantize(Decimal("0.01"))
        return attrs

    def _strip_non_model_flags(self, data: dict) -> None:
        # Remove flags we use for computation but which are not model fields
        data.pop("tax_enabled", None)
        data.pop("tax_rate", None)

    def create(self, validated):
        self._ensure_supplier(validated)
        self._strip_non_model_flags(validated)        # ← NEW

        items_data = validated.pop("items", [])
        user = self.context["request"].user
        validated["created_by"] = user
        if not validated.get("lpo_number"):
            from .services import next_lpo_number
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

        if "supplier" in validated or "supplier_name" in validated:
            self._ensure_supplier(validated)
        self._strip_non_model_flags(validated)        # ← NEW

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